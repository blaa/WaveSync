import asyncio
from libwavesync import time_machine

class SampleReader(asyncio.Protocol):
    """Read samples over the network, chunk them and put into a queue"""

    # Number of empty chunks before silence is detected.
    SILENCE_TRESHOLD = 20
    HEADER_SIZE = 4

    def __init__(self, audio_config):
        super().__init__()
        self.sample_queue = asyncio.Queue()

        self.audio_config = audio_config

        self.silence_detect = 0

        # Initialized along the chunk_size
        self._payload_size = None

        # Buffering before chunking
        self.buffer = None

        # Tracking stream time.
        self.stream_time = None

    @property
    def payload_size(self):
        return self._payload_size

    @payload_size.setter
    def payload_size(self, payload_size):
        "Calculate optimal chunk size"
        # 1420 is max payload for UDP over 1500 MTU ethernet
        # 80 - max IP header (60) + UDP header.
        # 4 - our header / timestamp
        # NOTE: 60 bytes is pessimistically large IP header. Could be as
        #       small as 20 bytes.

        # Remove our header from the max payload size
        self._payload_size = payload_size
        max_chunk_size = payload_size - self.HEADER_SIZE
        self.audio_config.chunk_size = max_chunk_size

    def connection_made(self, transport):
        "Initialize stream buffer"
        self.buffer = bytes()

    def data_received(self, data):
        "Read fifo indefinitely and push data into queue"

        # TODO: Buffer needs to be only twice the size of the data
        # and could be handled without allocations/deallocations.
        # TODO: Use bytearray()
        self.buffer += data

        while len(self.buffer) >= self.audio_config.chunk_size:
            chunk = self.buffer[:self.audio_config.chunk_size]
            self.buffer = self.buffer[self.audio_config.chunk_size:]

            # Detect the end of current silence
            if self.silence_detect is True:
                if any(chunk):
                    self.silence_detect = 0
                    print("Silence - end")
                    now = time_machine.now()
                    if not self.stream_time or self.stream_time < now:
                        self.stream_time = now
                else:
                    # Still silence
                    continue
            else:
                # Heuristic detection of silence start
                if chunk[0] == 0 and chunk[-1] == 0:
                    self.silence_detect += 1
                else:
                    self.silence_detect = 0

                # Silence too long - stop transmission
                if self.silence_detect > self.SILENCE_TRESHOLD:
                    if any(chunk): # Accurate check
                        self.silence_detect = 0
                    else:
                        print("Silence - start")
                        self.silence_detect = True
                        continue

            if self.stream_time is None:
                self.stream_time = time_machine.now()
            else:
                self.stream_time += self.audio_config.chunk_time
            self.sample_queue.put_nowait((self.stream_time, chunk))

        # Warning - might happen on slow UDP output sink
        if self.sample_queue.qsize() > 600:
            s = "WARNING: Samples in queue: %d - slow UDP transmission or eager input."
            s = s % self.sample_queue.qsize()
            print(s)

        if self.stream_time is not None:
            diff = self.stream_time - time_machine.now()
            if diff < min(-self.audio_config.latency_ms/2, -1):
                print("WARNING: Input underflow.")
                self.stream_time = None

    def connection_lost(self, exc):
        print("The pulse was lost. I should go.")
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(loop.stop)

    def decrement_payload_size(self):
        "Decrement chunk size and flush chunks currently in queue"
        self.payload_size -= 1
        # Empty the queue
        while True:
            try:
                self.sample_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return self.audio_config.chunk_size + self.HEADER_SIZE

    def get_next_chunk(self):
        return self.sample_queue.get()
