import asyncio
import random
from time import time
from libwavesync import time_machine


class ChunkPlayer:
    "Play received audio and keep sync"

    def __init__(self, chunk_queue, receiver, tolerance_ms,
                 buffer_size, device_index):
        # Our data source
        self.chunk_queue = chunk_queue

        # Required for showing unified stats.
        self.receiver = receiver

        # Configuration
        self.tolerance_ms = tolerance_ms

        # Audio state
        self.audio_config = None
        self.buffer_size = buffer_size
        self.device_index = device_index
        self.stream = None

        # Generate silence frames (zeroed) of appropriate sizes for chunks
        self.silence_cache = None

        # Number of silent frames that need to be inserted to get in sync
        self.silence_to_insert = 0

        # Stats
        self.stat_time_drops = 0
        self.stat_output_delays = 0
        self.stat_total_delay = 0

        # Stream
        self.pyaudio = None

        # Used to quit main loop
        self.stop = False

        # Calculated sizes
        self.frame_size = None
        self.chunk_frames = None

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

    def get_silent_chunk(self):
        "Generate and cache silent chunks"
        if self.silence_cache is not None:
            return self.silence_cache

        silent_chunk = b'\x00' * self.audio_config.chunk_size
        self.silence_cache = silent_chunk
        return silent_chunk

    def _open_stream(self):
        import pyaudio

        self.clear_state()

        # Open stream
        cfg = self.audio_config

        frames_per_buffer = self.buffer_size

        if self.device_index == -1:
            # We are tested. Don't open stream, but calculate chunk_frames.
            pass
        else:
            assert self.stream is None
            self.pyaudio = pyaudio.PyAudio()
            audio_format = (
                pyaudio.paInt24
                if cfg.sample == 24
                else pyaudio.paInt16
            )
            stream = self.pyaudio.open(output=True,
                                       channels=cfg.channels,
                                       rate=cfg.rate,
                                       format=audio_format,
                                       frames_per_buffer=frames_per_buffer,
                                       output_device_index=self.device_index)
            self.stream = stream

        self.chunk_frames = self.audio_config.chunk_size / cfg.frame_size

    def _close_stream(self):
        self.stream.stop_stream()
        self.stream.close()
        self.pyaudio.terminate()
        self.stream = None


    async def chunk_player(self):
        "Reads asynchronously chunks from the list and plays them"

        cnt = 0

        # Chunk/s stat
        recent_start = time()
        recent = 0

        mid_tolerance_s = self.tolerance_ms / 2 / 1000
        one_ms = 1/1000.0

        max_delay = 5

        while not self.stop:
            if not self.chunk_queue.chunk_list:

                if self.audio_config is not None:
                    print("Queue empty - waiting")

                self.chunk_queue.chunk_available.clear()
                await self.chunk_queue.chunk_available.wait()

                recent_start = time()
                recent = 0
                if self.audio_config is not None:
                    await asyncio.sleep(self.audio_config.latency_ms / 1000 / 4)
                    print("Got stream flowing. q_len=%d" % len(self.chunk_queue.chunk_list))
                continue

            cmd, item = self.chunk_queue.chunk_list.popleft()

            if cmd == self.chunk_queue.CMD_CFG:
                print("Got new configuration - opening audio stream")
                self.clear_state()
                self.audio_config = item
                if self.stream:
                    self._close_stream()
                self._open_stream()
                # Calculate maximum sensible delay in given configuration
                max_delay = (2000 + self.audio_config.sink_latency_ms +
                             self.audio_config.latency_ms) / 1000
                print("Assuming maximum chunk delay of %.2fms in this setup" % (max_delay * 1000))
                continue
            elif cmd == self.chunk_queue.CMD_DROPS:
                if item > 200:
                    print("Recovering after a huge packet loss of %d packets" % item)
                    self.clear_state()
                else:
                    # Just slowly resync
                    self.silence_to_insert += item
                continue

            # CMD_AUDIO

            if self.stream is None:
                # No output, no playing.
                continue

            mark, chunk = item
            desired_time = mark - self.audio_config.sink_latency_ms

            # 0) We got the next chunk to be played
            now = time_machine.now()

            # Negative when we're lagging behind.
            delay = desired_time - now

            self.stat_total_delay += delay

            recent += 1
            cnt += 1

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
                    self.stat_time_drops += 1
                    continue

            elif delay > max_delay:
                # Probably we hanged for so long time that the time recovering
                # mechanism rolled over. Recover
                print("Huge recovery - delay of %.2f exceeds the max delay of %.2f" % (
                    delay, max_delay))
                self.clear_state()
                continue

            # If chunk is in the future - wait until it's within the tolerance
            elif delay > one_ms:
                to_wait = max(one_ms, delay - one_ms)
                await asyncio.sleep(to_wait)


            # Wait until we can write chunk into output buffer. This might
            # delay us too much - the probabilistic dropping mechanism will kick
            # in.
            times = 0
            while True:
                buffer_space = self.stream.get_write_available()
                if buffer_space < self.chunk_frames:
                    self.stat_output_delays += 1
                    await asyncio.sleep(one_ms)
                    times += 1
                    if times > 200:
                        print("Hey, the output is STUCK!")
                        await asyncio.sleep(1)
                        break
                    continue
                self.stream.write(chunk)
                break

            # Main status line
            if recent > 200:
                frames_in_chunk = len(chunk) / self.audio_config.frame_size

                took = time() - recent_start
                chunks_per_s = recent / took

                if self.receiver is not None:
                    network_latency = self.receiver.stat_network_latency
                    network_drops = self.receiver.stat_network_drops
                else:
                    network_latency = 0
                    network_drops = 0

                s = ("STAT: chunks: q_len=%-3d bs=%4.1f "
                     "ch/s=%5.1f "
                     "net lat: %-5.1fms "
                     "avg_delay=%-5.2f drops: time=%d net=%d out_delay=%d")
                s = s % (
                    len(self.chunk_queue.chunk_list),
                    buffer_space / frames_in_chunk,
                    chunks_per_s,
                    1000.0 * network_latency,
                    1000.0 * self.stat_total_delay/cnt,
                    self.stat_time_drops,
                    network_drops,
                    self.stat_output_delays,
                )
                print(s)

                recent = 0
                recent_start = time()

                # Warnings
                if self.receiver is not None:
                    if self.receiver.stat_network_latency > 1:
                        print("WARNING: Your network latency seems HUGE. "
                              "Are the clocks synchronised?")
                    elif self.receiver.stat_network_latency <= -0.05:
                        print("WARNING: You either exceeded the speed of "
                              "light or have unsynchronised clocks")

        print("- Finishing chunk player")
