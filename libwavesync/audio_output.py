from libwavesync import time_machine

class AudioOutput:
    """
    Output abstraction - wraps all methods of sound card required to work.
    """
    def __init__(self, config, device_index, buffer_size, callback):
        # Import pyaudio only if output is enabled.
        # pylint: disable=import-outside-toplevel
        import pyaudio

        self.stream = None
        self.pyaudio = None
        self.config = config

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

        def pa_cb(in_data, frame_count, time_info, status):
            "Wrap callback"
            data = callback(frame_count)
            assert len(data) == self.config.frame_size * frame_count
            return (data, pyaudio.paContinue)

        self.stream = self.pyaudio.open(output=True,
                                        channels=config.channels,
                                        rate=config.rate,
                                        format=audio_format,
                                        frames_per_buffer=buffer_size,
                                        output_device_index=device_index,
                                        stream_callback=pa_cb)

        # Calculated based on config
        self.silent_frame = b'\x00' * config.frame_size
        self.silent_buffer = self.silent_frame * buffer_size
        self.frame_time = 1.0 / config.rate
        self.buffer_time = buffer_size / config.rate

        if callback is not None:
            self.stream.start_stream()

    def __del__(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        if self.pyaudio:
            self.pyaudio.terminate()
            self.pyaudio = None

    def get_write_available(self):
        """
        Return number of whole frames we can write to the buffer.

        Works only when working without a callback.
        """
        return self.stream.get_write_available()

    def write(self, data):
        """Write to the stream - use only when without a callback"""
        ret = self.stream.write(data)
        return ret
