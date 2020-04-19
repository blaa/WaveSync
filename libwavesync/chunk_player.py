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

    def calculate_delay(self, mark):
        """
        Calculate a relative playback delay for an absolute time mark.
        """
        buffer_space = self.audio_output.get_write_available()
        now = time_machine.now()
        play_time = self.audio_output.get_play_time(buffer_space, now)
        delay = play_time - mark

        print("  DELAY: d={:.2f}ms now={} play_t={} p-n={:.2f}ms inbuf={}fr {}ms".format(
            delay*1000, now-1584456060, play_time-1584456060,
            (play_time-now) * 1000,

            self.audio_output.buffer_total - buffer_space,
            (self.audio_output.buffer_total - buffer_space) / 44100.0 * 1000 ))
        return delay

    async def read_presynchronized(self):
        """
        Initial audio synchronization step.

        - Read commands from the queue,
        - play silence if queue is empty
        - do initial synchronization when playback starts
        - do a harsh resynchronization if playback differs too much
        - return a chunk delay instead of the absolute "mark".
        """
        print("READ PRESYNCH q=", len(self.chunk_queue.chunk_list))
        item = await self.read_commands()
        if item is None:
            return None

        mark, chunk = item

        delay = self.calculate_delay(mark)
        # chunk delay > 0 -> too late
        # chunk delay < 0 -> too early
        if delay > 2 * self.audio_output.buffer_latency:
            # Major resynch until within the tolerance.
            print("  Too late: drop frames", delay, "buf", 2 * self.audio_output.buffer_latency*1000)
            while True:
                item = await self.read_commands()
                if item is None:
                    print("  run out of chunks")
                    return None
                mark, chunk = item
                delay = self.calculate_delay(mark)
                if delay <= self.tolerance_s:
                    print("  OK, enough", delay)
                    break
                print("  DROP", delay)

        elif delay < -2 * self.audio_output.buffer_latency:
            print("  Too early: wait in silence", delay, 2 * self.audio_output.buffer_latency*1000)
            if delay < -2:
                print("  Way too early mark - Clocks not synchronized probably?")
                return None
            await self.silence_until(mark)
            delay = self.calculate_delay(mark)
            print("  OK", delay)
        return delay, chunk

    async def silence_until(self, mark):
        "Asynchronuously play silence until we reach mark"
        while True:
            delay = self.calculate_delay(mark)
            if delay > -self.tolerance_s:
                print("  End of silence reached", delay)
                break
            self.audio_output.play_silence()
            await asyncio.sleep(self.audio_output.config.chunk_time)

    async def chunk_player(self):
        """
        Handles filling of the output buffer.

        - Keep the buffer full - play silence if there's no signal to play.
        - Do a "major" synchronization events to start playing or resynch after a network loss.
        - Do a minor synchronization based on a long-term error average.
        """

        # Initially we wait for configuration
        while not self.stop:
            await self.read_commands(block=True)
            if self.audio_output:
                break

        # Audio output is enabled and we have a stream configuration,
        # it can change though later.
        i = 0

        delay_sum = 0
        delay_count = 0
        delay_avg = 0

        # True if we are not playing synchronously right now and want to start
        # in the right moment.
        initial_recovery = False

        relative = time_machine.now()

        while not self.stop:
            # In general case we have a chunk to add to the output buffer every
            # `chunk_time` seconds.

            # Important:
            # Keep buffer FULL, even if you'd have to wait for synchronization

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


            # Wait, without blocking, for a place in the output buffer. The
            # buffer is emptied periodically in a larger chunks - rather than
            # often in small chunks.

            print()
            print()
            print("WAIT FOR BUF @ %.1f" % ((time_machine.now() - relative) * 1000))
            while True:
                available = self.audio_output.get_write_available()
                if available > self.audio_output.chunk_frames:
                    break
                await asyncio.sleep(1 * self.audio_output.config.chunk_time)
            print("WAIT DONE at %.1f: AUDIO_BUF_SPACE=%d chunks_in_queue=%d" %
                  ((time_machine.now()-relative) * 1000,
                  available, len(self.chunk_queue.chunk_list)))

            # Some data in the buffer were moved away, what is left has to be
            # played almost completely, mark the current time for latency
            # calculation
            now = time_machine.now()

            # The next chunk will start playing at this, more-less, point of
            # time.
            play_time = self.audio_output.get_play_time(available, now)

            # Fill all free space in the output buffer
            delay = 0
            while available > self.audio_output.chunk_frames:
                print("  ONE CHUNK INCOMING")
                item = await self.read_commands()

                if item is None:
                    # The audio must play.
                    print("  Playing silence - no commands")
                    self.audio_output.play_silence()
                    break

                mark, chunk = item

                delay = play_time - mark
                print("  CHUNK PLAY DELAY IS %.2fms" % (delay * 1000))
                print("  CHUNK PUT DELAY IS %.2fms %.2fms" % ((now - mark) * 1000,
                                                              (time_machine.now() - mark) * 1000))
                if delay > 3 * self.tolerance_s: # self.audio_output.buffer_latency:
                    print("  ** RECOV; lagging - DROP frame")
                    self.stats.time_drops += 1
                    continue

                if delay < -3 * self.tolerance_s: # self.audio_output.buffer_latency:
                    # Put it back. Play it later
                    print("  ** RECOV; early - Start INITIAL")
                    initial_recovery = True
                    delay_sum = 0
                    delay_count = 0

                if initial_recovery and delay < -self.tolerance_s:
                    print("  ** RECOV INITIAL; early - WAIT")
                    self.chunk_queue.chunk_list.appendleft((self.chunk_queue.CMD_AUDIO, item))
                    self.audio_output.play_silence()
                    break

                initial_recovery = False

                self.audio_output.write(chunk[:-4])
                available -= self.audio_output.chunk_frames

                # TODO: Maybe unneeded?
                play_time += self.audio_output.chunk_frames / self.audio_output.config.rate

                delay_sum += delay
                delay_count += 1

            # Plan the future
            print("PLANNING PHASE, should be low:", self.audio_output.get_write_available())
            print("LAST CHUNK DELAY", delay * 100)
            print("TOTAL BUFF", self.audio_output.buffer_total, self.audio_output.buffer_latency * 1000)
            if delay_count > 10:
                print("DELAY AVG", delay_sum/ delay_count * 1000)

            # We just filled the buffer, we can sleep a bit longer initially.
            #await asyncio.sleep(self.audio_output.buffer_latency / 3)
            await asyncio.sleep(self.audio_output.config.chunk_time)

        print("- Finishing chunk player")
