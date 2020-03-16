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
        self.stream = self.pyaudio.open(output=True,
                                        channels=config.channels,
                                        rate=config.rate,
                                        format=audio_format,
                                        frames_per_buffer=buffer_size,
                                        output_device_index=device_index)

        # It seems to be twice the size we set, independently on the number of channels
        self.buffer_total = self.get_write_available()

        print("BUFS", config.channels, buffer_size, self.buffer_total) # max_buffer seems twice the size; mono/stereo?
        print("CONFIG", config, config.chunk_time)

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
        return self.stream.write(data)

    def can_write_chunk(self):
        "Can we write at least a single chunk to the buffer without blocking?"
        return self.stream.get_write_available() >= self.chunk_frames

    def get_play_time(self, available, now):
        "What is the current play time of the chunk we would schedule now?"
        buffer_delay = (self.buffer_total - available) / self.config.rate
        return now + buffer_delay + self.config.sink_latency_s

    def play_silence(self, seconds):
        """
        Play silence
        TODO: Parametrized
        """
        chunk = self.get_silence()
        times = int(seconds * 1000)
        print("Playing silence", seconds * 1000, "ms")
        self.write(chunk * times)

        # Return number of frames written to the buffer
        return len(chunk) // self.config.frame_size * times

    def get_silence(self):
        "Generate and cache a 1ms silence chunk"
        if self.silence_cache is not None:
            return self.silence_cache

        frame = (
            b'\x00\x00\x00'
            if self.config.sample == 24
            else b'\x00\x00'
        )
        if self.config.channels == 2:
            frame = frame * 2

        frames = self.config.rate // 1000 + 1

        silent_chunk = frame * frames
        self.silence_cache = silent_chunk
        return silent_chunk
