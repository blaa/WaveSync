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

        # DEBUG / Backing
        self.next_buffer = None
        self.residual_frames = b""
        self.residual_frames_count = 0
        self.relative = time_machine.now()
        self.prev_callback = 0
        self.cb_periods = []
        self.next_period = 0
        self.initial_recovery = True

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
        self.audio_output = AudioOutput(audio_config, self.device_index,
                                        self.buffer_size,
                                        self.player_callback)
        # Calculate maximum sensible delay in given configuration
        self.max_delay = (2000 + self.audio_output.config.sink_latency_ms +
                          self.audio_output.config.latency_ms) / 1000
        print("Assuming maximum chunk delay of %.2fms in this setup" % (self.max_delay * 1000))

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

    async def peek_audio(self):
        """
        Peek at next audio command without removing it from the queue.
        """
        # Normal read, to proces other commands.
        item = await self.read_commands()
        if item is None:
            return None

        mark, audio = item

        # Put audio back.
        cmd = (self.chunk_queue.CMD_AUDIO, item)
        self.chunk_queue.chunk_list.appendleft(cmd)
        return mark, audio

    def drop_audio(self):
        "Drop single chunk of audio from queue"
        self.chunk_queue.chunk_list.popleft()

    def player_callback(self, frames_count):
        """
        Called in a separate thread by pyaudio to fill buffer.

        This call is a place to estimate the audio clock accuracy and drift.
        """
        now = time_machine.now()

        # Unlock the preparation of next buffer
        data = self.next_buffer

        # TODO: Sometimes TWO callbacks are called, one late, and then, one
        # early take (at least) that into account.

        # That's the hypothetical real moment if the clock was accurate
        stream_time = self.next_period + self.audio_output.buffer_time

        # Estimated based on the callback time (now).
        # now is synchronized with NTP among all playing devices.
        next_period_est = now + self.audio_output.buffer_time

        e = stream_time - next_period_est

        if abs(e) > 1:
            print(": HIGH CORRECTION")
            self.next_period = next_period_est
        else:
            self.next_period = (stream_time * 5 + next_period_est) / 6


        # Mark the next buffer as ready for preparation
        self.next_buffer = None

        if self.stop:
            return None

        # Calculate stats and stability estimates
        diff = now - self.prev_callback
        self.prev_callback = now
        self.cb_periods.append(diff)
        self.cb_periods = self.cb_periods[-5:]
        print(f": t={now:9.2f} d={1000*diff:5.2f}ms: Player callback called")
        print(f":   current error={1000*e:.2f}")
        avg = sum(self.cb_periods) / len(self.cb_periods)
        err = abs(diff - avg) # sum(abs(avg - v) for v in self.cb_periods)
        print(f":   periods: avg={avg} e={err}")
        print(f":   buf_time={1000*self.audio_output.buffer_time:.2f}")

        if data is None:
            print(": Playing silence")
            return self.audio_output.silent_buffer
        else:
            print(": Playing chunk", len(data))
            return data

    async def initial_sync(self, play_time):
        """
        Browse the command stream until we're initially synced.

        - Return 0 if we can play immediately (or if there are no chunks)
        - Return > 0 if we need to play that much silent frames before playing next chunk.
        - Drop chunk if it's late.
        """
        chunk_time = self.audio_output.config.chunk_time
        frame_time = self.audio_output.frame_time
        while True:
            item = await self.peek_audio()

            if item is None:
                # No commands, nothing to sync.
                return 0

            mark, _ = item

            # This is an estimate, hopefully best we can get. It can be
            # shifted from the real play time, but we hope that the shift
            # will be stable.
            delay = play_time - mark + self.audio_output.config.sink_latency_s
            # delay = play_time - mark + self.residual_frames_count * frame_time # TODO: Maybe?
            print(f"  CHUNK PLAY DELAY IS {1000*delay:.2f}ms tol {1000*self.tolerance_s:.2f}")

            if delay > 3 * self.tolerance_s and self.initial_recovery is False:
                print(f"  ** RECOV INIT: delay={1000*delay}ms is > 3*tolerance")
                self.initial_recovery = True

            if self.initial_recovery and delay > chunk_time:
                print(f"  ** RECOV DROP LATE; delay={1000*delay}ms q={len(self.chunk_queue.chunk_list)}")
                self.drop_audio()
                self.stats.time_drops += 1
                continue

            if delay < -3 * self.tolerance_s: # self.audio_output.buffer_latency:
                wait_frames = int(-delay / frame_time)
                print(f"  ** RECOV WAIT EARLY: delay={1000*delay}ms silent frames={wait_frames}")
                self.initial_recovery = False
                return wait_frames

            # We are mostly synced (within 3*tolerance)
            if self.initial_recovery is True:
                print("  ** RECOV FINISH")
            self.initial_recovery = False

            return 0

    async def chunks_to_buffer(self, silence):
        """
        Convert series of chunks from the input queue into a playback buffer.
        """
        frame_size = self.audio_output.config.frame_size
        chunk_frames = self.audio_output.chunk_frames

        frames_to_go = self.buffer_size - self.residual_frames_count

        # Start with residual frames
        chunks = [self.residual_frames]

        print(f"c2b: Preparing chunk buffer chunk_frames={chunk_frames} "
              f"frame_size={frame_size} to_go={self.buffer_size}")

        print("  c2b: Start residual", self.residual_frames_count)

        self.residual_frames = b""
        self.residual_frames_count = 0

        # Then add silence to wait for the next chunk playback
        silence_to_go = min(silence, frames_to_go)
        if silence_to_go > 0:
            print(f"  c2b: Add {silence_to_go} silence of {silence}")
            chunks.append(self.audio_output.silent_frame * silence_to_go)
            frames_to_go -= silence_to_go

        # Then fill wi3th audio data from chunks
        mark = None
        while frames_to_go > 0:
            item = await self.read_commands()

            if item is None:
                # The audio must play - Fill with silence.
                chunks.append(self.audio_output.silent_frame * frames_to_go)
                print("  c2b: No chunks left - fill with silence")
                break

            mark, chunk = item

            if chunk_frames > frames_to_go:
                cut = frames_to_go * frame_size
                play_frames, self.residual_frames = chunk[:cut], chunk[cut:]
                self.residual_frames_count = chunk_frames - frames_to_go # len(self.residual_frames) // frame_size
                chunks.append(play_frames)
                print(f"  c2b: add partial chunk size={len(chunk)} cut={cut} to_go={frames_to_go} "
                      f" play/residual={len(play_frames)}/{len(self.residual_frames)}")
                frames_to_go = 0
            else:
                print(f"  c2b: Add full chunk, to_go={frames_to_go} q={len(self.chunk_queue.chunk_list)}")
                frames_to_go -= chunk_frames
                chunks.append(chunk)

        next_buffer = b"".join(chunks)
        print(f"  c2b: NEXT IS {len(next_buffer)}")
        return mark, next_buffer


    async def chunk_player(self):
        """
        Handles filling of the output buffer.

        - Keep the buffer full - play silence if there's no signal to play.
        - Do a "major" synchronization events to start playing or resynch after a network loss.
        - Do a minor synchronization based on a long-term error average.
        """
        # Initially we wait for audio configuration
        while not self.stop:
            await self.read_commands(block=True)
            if self.audio_output:
                break

        # Audio output is enabled and we have a stream configuration,
        # it can change later.

        if self.audio_output.buffer_time > self.audio_output.config.latency_s:
            print("Buffer time is bigger than our latency!")

        while not self.stop:

            # In general case we have a chunk to add to the output buffer every
            # `chunk_time` seconds.

            # Important:
            # Keep buffer FULL, even if you'd have to wait for synchronization

            # - Detach stream control from chunks. Observe average delay over time
            #   and adjust time by requesting drop/silence injection
            # - During initial audio start try to sync though.
            # - This can allow PID like approach to synchro.

            if self.next_buffer is not None:
                print("Waiting until next buffer is consumed")
                await asyncio.sleep(5 * self.audio_output.config.chunk_time)
                continue

            print()
            print()
            print()
            print("Building buffer: Initial sync")
            silence = await self.initial_sync(play_time=self.next_period)

            last_mark, self.next_buffer = await self.chunks_to_buffer(silence)

            print("Chunk buf DONE")


        print("- Finishing chunk player")
