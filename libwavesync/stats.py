from time import time

class Stats:
    """
    Aggregate statistics from all components and display periodically
    """
    def __init__(self):
        # Chunk counter
        self.chunks = 0
        self.start = time()

        # Player stats
        self.time_drops = 0
        self.output_delays = 0
        self.total_delay = 0
        self.total_chunks = 0

        # Receiver stats
        self.network_latency = 0
        self.network_drops = 0

    def show(self, queue_length):
        "Display statistics"
        took = time() - self.start
        chunks_per_s = self.chunks / took

        s = ("STAT: chunks: q_len=%-3d "
             "ch/s=%5.1f "
             "net lat: %-5.1fms "
             "avg_delay=%-5.2f drops: time=%d net=%d out_delay=%d")

        s = s % (
            queue_length,
            chunks_per_s,
            1000.0 * self.network_latency,
            1000.0 * self.total_delay/self.total_chunks,
            self.time_drops,
            self.network_drops,
            self.output_delays,
        )
        print(s)

        # Warnings
        if self.network_latency > 1:
            print("WARNING: Your network latency seems HUGE. "
                  "Are the clocks synchronised?")
        elif self.network_latency <= -0.05:
            print("WARNING: You either exceeded the speed of "
                  "light or have unsynchronised clocks")

    def chunk(self, queue_length):
        """
        Count new chunk and maybe print statistics
        """
        self.chunks += 1
        if self.chunks > 200:
            self.show(queue_length)
            self.chunks = 0
            self.start = time()
