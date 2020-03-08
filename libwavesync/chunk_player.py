import asyncio
import random
from libwavesync import (
    time_machine,
    AudioOutput
)


class ChunkPlayer:
    "Play received audio and keep sync"

    def __init__(self, chunk_queue, stats, tolerance_ms,
                 buffer_size, device_index):
        # Our data source
        self.chunk_queue = chunk_queue

        # Unified statistics
        self.stats = stats

        # Configuration
        self.tolerance_ms = tolerance_ms

        # Audio state
        self.buffer_size = buffer_size
        self.device_index = device_index
        self.audio_output = None
        self.max_delay = 5

        # Number of silent frames that need to be inserted to get in sync
        self.silence_to_insert = 0

        # Used to quit main loop
        self.stop = False

        # Calculated sizes
        self.frame_size = None

    def clear_state(self):
        "Clear player queue"
        self.silence_to_insert = 0

        # Clear the chunk list, but preserve CFG commands
        cfg = None
        for cmd, item in self.chunk_queue.chunk_list:
            if cmd == self.chunk_queue.CMD_CFG:
                cfg = item
                break

        self.chunk_queue.chunk_list.clear()
        if cfg is not None:
            self.chunk_queue.chunk_list.append((self.chunk_queue.CMD_CFG, cfg))

        self.chunk_queue.do_recovery()

    def _handle_cmd_drops(self, item):
        "Handle drops-detected command"
        if item > 200:
            print("Recovering after a huge packet loss of %d packets" % item)
            self.clear_state()
        else:
            # Just slowly resync
            self.silence_to_insert += item

    def _handle_cmd_cfg(self, audio_config):
        "Handle configuration command"
        print("Got new configuration - opening audio stream")
        self.clear_state()
        del self.audio_output
        self.audio_output = AudioOutput(audio_config, self.device_index, self.buffer_size)
        # Calculate maximum sensible delay in given configuration
        self.max_delay = (2000 + self.audio_output.config.sink_latency_ms +
                          self.audio_output.config.latency_ms) / 1000
        print("Assuming maximum chunk delay of %.2fms in this setup" % (self.max_delay * 1000))

    async def _handle_empty_queue(self):
        "Handle case with the empty input queue"
        if self.audio_output is not None:
            print("Queue empty - waiting")

        self.chunk_queue.chunk_available.clear()
        # FIXME: This blocks. But instead we should be pumping data into output buffer.
        await self.chunk_queue.chunk_available.wait()

        if self.audio_output is not None:
            await asyncio.sleep(self.audio_output.config.latency_ms / 1000 / 4)
            print("Got stream flowing. q_len=%d" % len(self.chunk_queue.chunk_list))

    async def _handle_cmd_audio(self, item):
        "Handle chunk playback"
        mid_tolerance_s = self.tolerance_ms / 2 / 1000
        one_ms = 1/1000.0

        mark, chunk = item
        desired_time = mark - self.audio_output.config.sink_latency_s

        # 0) We got the next chunk to be played
        now = time_machine.now()

        # Negative when we're lagging behind.
        delay = desired_time - now

        self.stats.total_delay += delay
        self.stats.total_chunks += 1

        # Probabilistic drop of lagging chunks to get back on track.
        # Probability of drop is higher, the more chunk lags behind current
        # time. Similar to the RED algorithm in TCP congestion.
        if delay < -mid_tolerance_s:
            over = -delay - mid_tolerance_s
            prob = over / mid_tolerance_s
            if random.random() < prob:
                s = "Drop chunk: q_len=%2d delay=%.1fms < 0. tolerance=%.1fms: P=%.2f"
                s = s % (len(self.chunk_queue.chunk_list),
                         delay * 1000, self.tolerance_ms, prob)
                print(s)
                self.stats.time_drops += 1
                return

        elif delay > self.max_delay:
            # Probably we hanged for so long time that the time recovering
            # mechanism rolled over. Recover
            print("Huge recovery - delay of %.2f exceeds the max delay of %.2f" % (
                delay, self.max_delay))
            self.clear_state()
            return

        # If chunk is in the future - wait until it's within the tolerance
        elif delay > one_ms:
            to_wait = max(one_ms, delay - one_ms)
            await asyncio.sleep(to_wait)

        # Wait until we can write chunk into output buffer. This might
        # delay us too much - the probabilistic dropping mechanism will kick
        # in.
        times = 0
        while True:
            buffer_space = self.audio_output.get_write_available()
            if buffer_space < self.audio_output.chunk_frames:
                self.stats.output_delays += 1
                await asyncio.sleep(one_ms)
                times += 1
                if times > 200:
                    print("Hey, the output is STUCK!")
                    await asyncio.sleep(1)
                    break
                continue
            self.audio_output.write(chunk)
            return

    async def chunk_player(self):
        "Reads asynchronously chunks from the list and plays them"

        while not self.stop:
            if not self.chunk_queue.chunk_list:
                await self._handle_empty_queue()
                continue

            cmd, item = self.chunk_queue.chunk_list.popleft()

            if cmd == self.chunk_queue.CMD_CFG:
                self._handle_cmd_cfg(item)
                continue

            if cmd == self.chunk_queue.CMD_DROPS:
                self._handle_cmd_drops(item)
                continue

            # CMD_AUDIO

            if self.audio_output is None:
                # No output, no playing.
                continue

            await self._handle_cmd_audio(item)

            # Main status line
            self.stats.chunk(queue_length=len(self.chunk_queue.chunk_list))

        print("- Finishing chunk player")
