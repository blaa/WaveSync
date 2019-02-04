import asyncio


class SampleReader(asyncio.Protocol):
    """Read samples over the network, chunk them and put into a queue"""

    # Number of empty chunks before silence is detected.
    SILENCE_TRESHOLD = 20
    HEADER_SIZE = 4

    def __init__(self):
        super().__init__()
        self.sample_queue = asyncio.Queue()

        self.silence_detect = 0

        # Initialized along the chunk_size
        self.chunk_size = None
        self.sample_size = None
        self.rate = None
        self.chunk_time = None

        # Buffering before chunking
        self.buffer = None

        # Tracking stream time.
        self.stream_time = None

    def _configure(self, chunk_size):
        "Configure fields, given a new chunk_size"
        self.chunk_size = chunk_size
        # To fit always the same amount of both channels (to not swap them in case
        # of a packet drop) ensure the amount of space is divisible by sample_size
        self.chunk_size -= self.chunk_size % self.sample_size
        samples_in_chunk = self.chunk_size / self.sample_size
        self.chunk_time = samples_in_chunk / self.rate

    def set_chunk_size(self, payload_size, sample_size, rate):
        "Calculate optimal chunk size"

        # Required for MTU detection and time tracking
        self.sample_size = sample_size
        self.rate = rate

        # 1420 is max payload for UDP over 1500 MTU ethernet
        # 80 - max IP header (60) + UDP header.
        # 4 - our header / timestamp
        # NOTE: 60 bytes is pessimistically large IP header. Could be as
        #       small as 20 bytes.

        # Remove our header from the max payload size
        self._configure(payload_size - self.HEADER_SIZE)

    def connection_made(self, transport):
        "Initialize stream buffer"
        self.buffer = bytes()

    def data_received(self, data):
        "Read fifo indefinitely and push data into queue"

        # NOTE: Buffer needs to be only twice the size of the data
        # and could be handled without allocations/deallocations.
        self.buffer += data

        while len(self.buffer) >= self.chunk_size:
            chunk = self.buffer[:self.chunk_size]
            self.buffer = self.buffer[self.chunk_size:]

            # Detect the end of current silence
            if self.silence_detect is True:
                if any(chunk):
                    self.silence_detect = 0
                    print("Silence - end")
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
                        self.stream_time = None
                        continue

            self.sample_queue.put_nowait(chunk)

        # Warning - might happen on slow UDP output sink
        if self.sample_queue.qsize() > 30:
            s = "WARNING: Samples in queue: %d - slow UDP transmission!"
            s = s % self.sample_queue.qsize()
            print(s)

    def connection_lost(self, exc):
        print("The pulse was lost. I should go.")
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(loop.stop)

    def decrement_chunk_size(self):
        "Decrement chunk size and flush chunks currently in queue"
        self._configure(self.chunk_size - 1)
        # Empty the queue
        while True:
            try:
                self.sample_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return self.chunk_size + self.HEADER_SIZE

    def get_chunk_size(self):
        return self.chunk_size

    def get_next_chunk(self):
        return self.sample_queue.get()
