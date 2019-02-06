"Audio configuration and related parameters"


class AudioConfig:
    """
    Maintains audio configuration and related calculations.

    Stores sample format, output latency.

    Vocab:
      Sample: 1-channel 16 or 24 bit number.
      Frame: 2 samples for stereo, 1 sample for mono.
    """

    def __init__(self, rate, sample, channels, latency_ms, sink_latency_ms):
        # Usually 44100 or 48000Hz
        self.rate = rate
        self.sample = sample
        self.channels = channels

        # System latency (distributed buffer size)
        self.latency_ms = latency_ms
        self.latency_s = latency_ms / 1000.0

        # Audio output latency
        self.sink_latency_ms = sink_latency_ms
        self.sink_latency_s = sink_latency_ms / 1000.0

        assert channels in [1, 2]
        assert sample in [24, 16]

        # Will be set later and can be decremented live, if the MTU doesn't
        # allow this big packets.
        self._chunk_size = None
        self.chunk_time = None

        # Calculate related
        self.frame_size = channels * sample // 8

    def __eq__(self, other):
        """
        Compare audio configuration.

        True normally means that the player output should be reconfigured.
        """
        keys = [
            'rate', 'sample', 'channels',
            'latency_ms', 'sink_latency_ms',
            'chunk_size',
        ]
        if other is None:
            return False
        for key in keys:
            if getattr(self, key) != getattr(other, key):
                return False

        return True

    @property
    def chunk_size(self):
        return self._chunk_size

    @chunk_size.setter
    def chunk_size(self, size):
        """
        Set chunk size correctly and calculate related chunk_time.
        """
        # To fit always the same amount of both channels (to not swap them in case
        # of a packet drop) ensure the amount of space is divisible by sample_size
        self._chunk_size = size
        self._chunk_size -= size % self.frame_size

        frames_in_chunk = self._chunk_size // self.frame_size
        self.chunk_time = frames_in_chunk / self.rate

    def __repr__(self):
        "Format for debugging"
        s = "<AudioConfig {}Hz {}bits {} latency={}ms sink={}ms size chunk={} frame={}>"
        return s.format(
            self.rate, self.sample,
            "stereo" if self.channels == 2 else "mono",
            self.latency_ms, self.sink_latency_ms,
            self.chunk_size, self.frame_size
        )
