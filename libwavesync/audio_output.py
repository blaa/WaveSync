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

        self.chunk_frames = config.chunk_size / config.frame_size

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

        self.max_buffer = self.get_write_available()

        print("BUFS", buffer_size, self.max_buffer) # max_buffer seems twice the size; mono/stereo?
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
        return self.stream.get_write_available()

    def write(self, data):
        return self.stream.write(data)

    def get_silent_chunk(self):
        "Generate and cache silent chunks"
        if self.silence_cache is not None:
            return self.silence_cache

        silent_chunk = b'\x00' * self.config.chunk_size
        self.silence_cache = silent_chunk
        return silent_chunk
