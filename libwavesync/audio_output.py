from libwavesync import time_machine

class AudioOutput:
    """
    Output abstraction - wraps all methods of sound card required to work.
    """
    def __init__(self, config, device_index, buffer_size):
        # Import pyaudio only if really needed.
        # pylint: disable=import-outside-toplevel
        import pyaudio

        self.stream = None
        self.pyaudio = None
        self.config = config

        # Generate silence frames (zeroed) of appropriate sizes for chunks
        self.silence_cache = None

        self.chunk_frames = config.chunk_size // config.frame_size

        if device_index == -1:
            # We are tested. Don't open stream (stop at calculation of chunk_frames).
            return

        assert self.stream is None
        self.pyaudio = pyaudio.PyAudio()

        if device_index is None:
            host_info = self.pyaudio.get_host_api_info_by_index(0)
            device_index = host_info['defaultOutputDevice']
            print("Using default output device index", device_index)

        audio_format = (
            pyaudio.paInt24
            if config.sample == 24
            else pyaudio.paInt16
        )

        self.silent_frame = b'\x00' * config.frame_size

        self.stream = self.pyaudio.open(output=True,
                                        channels=config.channels,
                                        rate=config.rate,
                                        format=audio_format,
                                        frames_per_buffer=buffer_size,
                                        output_device_index=device_index)

        # It seems to be twice the size we set, independently on the number of channels
        self.buffer_total = self.get_write_available()

        self.stream_time = None
        frame_time = 1.0 / config.rate
        self.byte_time = frame_time / config.frame_size
        self.buffer_latency = self.buffer_total * frame_time

    def __del__(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        if self.pyaudio:
            self.pyaudio.terminate()
            self.pyaudio = None

    def get_write_available(self):
        "Return number of whole frames we can write to the buffer"
        return self.stream.get_write_available()

    def write(self, data):
        ret = self.stream.write(data)
        if self.stream_time is None:
            self.stream_time = time_machine.now()
        else:
            self.stream_time += self.byte_time * len(data)
        return ret

    def can_write_chunk(self):
        "Can we write at least a single chunk to the buffer without blocking?"
        return self.stream.get_write_available() >= self.chunk_frames

    def get_play_time(self, available, now):
        "What is the current play time of the chunk we would schedule now?"
        buffer_delay = (self.buffer_total - available) / self.config.rate
        print("%d - %d = %d / %d -> %.2fms" % (
            self.buffer_total, available,
            self.buffer_total - available,
            self.config.rate, buffer_delay * 1000
            ))

        return now + buffer_delay + self.config.sink_latency_s

    def play_silence_s(self, seconds):
        """
        Play silence
        TODO: Parametrized
        """
        chunk = self._get_silence()
        times = int(seconds * 1000)
        print("    Playing silence", seconds * 1000, "ms")
        self.write(chunk * times)

        # Return number of frames written to the buffer
        return len(chunk) // self.config.frame_size * times

    def play_silence(self):
        "Fill output buffer with silence"
        frames = self.silent_frame * self.get_write_available()
        self.write(frames)

    def _get_silence(self):
        "Generate and cache a 1ms silence chunk"
        if self.silence_cache is not None:
            return self.silence_cache

        frames = self.config.rate // 1000 + 1

        silent_chunk = self.silent_frame * frames
        self.silence_cache = silent_chunk
        return silent_chunk
