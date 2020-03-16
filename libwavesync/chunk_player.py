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
        self.tolerance_s = tolerance_ms / 1000.0

        # Audio state
        self.buffer_size = buffer_size
        self.device_index = device_index
        self.audio_output = None
        self.max_delay = 5

        # Used to quit main loop
        self.stop = False

        # Calculated sizes
        self.frame_size = None

    def clear_state(self):
        "Clear player queue"

        # Clear the chunk list, but preserve CFG command
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
            # NOTE: There was silence insertion here, now, it should be automatic.
            pass

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

    # async def _handle_empty_queue(self):
    #     "Handle case with the empty input queue"
    #     if self.audio_output is not None:
    #         print("Queue empty - waiting")

    #     self.chunk_queue.chunk_available.clear()
    #     # FIXME: This blocks. But instead we should be pumping data into output buffer.
    #     await self.chunk_queue.chunk_available.wait()

    #     if self.audio_output is not None:
    #         await asyncio.sleep(self.audio_output.config.latency_ms / 1000 / 4)
    #         print("Got stream flowing. q_len=%d" % len(self.chunk_queue.chunk_list))

    # async def _handle_cmd_audio(self, item):
    #     "Handle chunk playback"
    #     mid_tolerance_s = self.tolerance_ms / 2 / 1000
    #     one_ms = 1/1000.0

    #     mark, chunk = item
    #     desired_time = mark - self.audio_output.config.sink_latency_s

    #     # Calculate latency caused by buffer
    #     a = self.audio_output.get_write_available()
    #     # Assuming this is in full "stereo frames"
    #     in_buffer = (self.audio_output.buffer_total - a)
    #     buffer_delay = in_buffer / self.audio_output.config.rate
    #     desired_time -= buffer_delay

    #     # 0) We got the next chunk to be played
    #     now = time_machine.now()

    #     # Negative when we're lagging behind.
    #     delay = desired_time - now

    #     self.stats.total_delay += delay
    #     self.stats.total_chunks += 1

    #     # TODO: Simplify and remove the max_delay maybe?
    #     if delay > self.max_delay:
    #         # Probably we hanged for so long time that the time recovering
    #         # mechanism rolled over. Recover
    #         print("Huge recovery - delay of %.2f exceeds the max delay of %.2f" % (
    #             delay, self.max_delay))
    #         self.clear_state()
    #         return

    #     # If chunk is in the future - wait until it's within the tolerance
    #     elif delay > one_ms:
    #         to_wait = max(one_ms, delay - one_ms)
    #         # FIXME: With empty buffer this should be inputting SILENCE
    #         await asyncio.sleep(to_wait)

    #     # Wait until we can write chunk into output buffer. This might
    #     # delay us too much - the probabilistic dropping mechanism will kick
    #     # in.
    #     times = 0
    #     while True:
    #         buffer_space = self.audio_output.get_write_available()
    #         if buffer_space < self.audio_output.chunk_frames:
    #             # Not enough space in buffer to add chunk.

    #             # Probabilistic drop of lagging chunks to get back on track.
    #             # Probability of drop is higher, the more chunk lags behind current
    #             # time. Similar to the RED algorithm in TCP congestion.
    #             if delay < -mid_tolerance_s:
    #                 over = -delay - mid_tolerance_s
    #                 prob = over / mid_tolerance_s
    #                 if random.random() < prob:
    #                     s = "Drop chunk: q_len=%2d delay=%.1fms < 0. tolerance=%.1fms: P=%.2f"
    #                     s = s % (len(self.chunk_queue.chunk_list),
    #                              delay * 1000, self.tolerance_ms, prob)
    #                     print(s)
    #                     self.stats.time_drops += 1
    #                     return

    #             self.stats.output_delays += 1
    #             await asyncio.sleep(10 * one_ms)
    #             times += 1
    #             if times > 200:
    #                 print("Hey, the output is STUCK!")
    #                 await asyncio.sleep(1.0)
    #                 break
    #             continue
    #         self.audio_output.write(chunk)
    #         return

    # async def chunk_player_orig(self):
    #     "Reads asynchronously chunks from the list and plays them"

    #     while not self.stop:
    #         if not self.chunk_queue.chunk_list:
    #             await self._handle_empty_queue()
    #             continue

    #         cmd, item = self.chunk_queue.chunk_list.popleft()

    #         if cmd == self.chunk_queue.CMD_CFG:
    #             self._handle_cmd_cfg(item)
    #             continue

    #         if cmd == self.chunk_queue.CMD_DROPS:
    #             self._handle_cmd_drops(item)
    #             continue

    #         # CMD_AUDIO

    #         if self.audio_output is None:
    #             # No output, no playing.
    #             continue

    #         await self._handle_cmd_audio(item)

    #         # Main status line
    #         self.stats.chunk(queue_length=len(self.chunk_queue.chunk_list))

    #     print("- Finishing chunk player")


    async def read_commands(self, block=False):
        """
        Read next entry from chunk_list, handle transparently all non-audio
        commands and return a next audio chunk if available.
        """
        if block and not self.chunk_queue.chunk_list:
            self.chunk_queue.chunk_available.clear()
            await self.chunk_queue.chunk_available.wait()

        while self.chunk_queue.chunk_list:
            cmd, item = self.chunk_queue.chunk_list.popleft()

            if cmd == self.chunk_queue.CMD_CFG:
                self._handle_cmd_cfg(item)
                continue

            if cmd == self.chunk_queue.CMD_DROPS:
                self._handle_cmd_drops(item)
                continue

            if cmd == self.chunk_queue.CMD_AUDIO:
                self.stats.total_chunks += 1

                # Main status line
                self.stats.chunk(queue_length=len(self.chunk_queue.chunk_list))

                return item

            assert False, "Invalid command received"

        # No audio in queue
        return None

    async def chunk_player(self):
        "Reads asynchronously chunks from the list and plays them"
        # Initially we wait for configuration
        while not self.stop:
            await self.read_commands(block=True)
            if self.audio_output:
                break

        # Audio output is enabled and we have a stream configuration,
        # it can change though later.
        i = 0
        while not self.stop:
            # In general case we have a chunk to add to the output buffer every
            # `chunk_time` seconds.

            # New idea 1:
            # 1) Wait until we can write at least one chunk
            # 2) Read chunks until first with correct tolerances
            # 3) Fill buffer with chunks (what if some are missing?)
            # 4) When buffer is full we have time to probably drop some more chunks

            # New idea 2:
            # - Detach stream control from chunks. Observe average delay over time
            #   and adjust time by requesting drop/silence injection
            # - During initial audio start try to sync though.
            # - This can allow PID like approach to synchro.

            # Feed output buffer with as many chunks as you can.

            # Save time and buffer status at the same moment
            buffer_space = self.audio_output.get_write_available()
            now = time_machine.now()

            while True:
                if buffer_space < self.audio_output.chunk_frames:
                    # Too little space for a single chunk - we're done
                    break

                # We either play silence, or some chunk.
                item = await self.read_commands()
                if item is None:
                    print("No commands - Silence.")
                    frames = self.audio_output.play_silence(2 * self.audio_output.config.chunk_time)
                    buffer_space -= frames
                    continue

                mark, chunk = item
                i += 1

                play_time = self.audio_output.get_play_time(buffer_space, now)
                delay = play_time - mark
                # chunk delay > 0 -> too late
                # chunk delay < 0 -> too early

                if delay > self.tolerance_s:
                    # Too late - DROP chunk
                    self.stats.time_drops += 1
                    print("DROP chunk={} delay={:.2f}ms tol={:.2f}ms bufspace={} ".format(
                        i,
                        delay*1000, self.tolerance_s*1000, buffer_space))
                    continue
                if delay < -self.tolerance_s:
                    # Too early - Wait while generating silence
                    silence = min(-delay - self.tolerance_s / 2, 20 / 1000)

                    frames = self.audio_output.play_silence(silence)
                    print("WAIT WITH SILENCE delay={:.2f}ms, frames={}/space={} silence={:.2f}ms".format(delay*1000, frames, buffer_space, silence*1000))
                    buffer_space -= frames
                    # BUG FIXME: This might've blocked! TODO: WE SHOULD WAIT WITH A LOOP!
                    # await asyncio.sleep(silence)
                print("PLAY cnt={} buff_space={} delay={:.2f}".format(
                    i, buffer_space, delay*100))
                self.audio_output.write(chunk)
                buffer_space -= len(chunk) // self.audio_output.config.frame_size

            print("Waiting for output buffer", buffer_space)
            await asyncio.sleep(6.0 * self.audio_output.config.chunk_time)
        print("- Finishing chunk player")
