import asyncio
from collections import deque


class ChunkQueue:
    "Queue of packets"

    CMD_AUDIO = 1
    CMD_DROPS = 2
    CMD_CFG = 3

    def __init__(self):
        # NOTE: On LAN an unsorted deque works for me. Might need
        # a packet ordering based on time mark eventually.
        self.chunk_list = deque()

        self.chunk_available = asyncio.Event()

        # When doing huge recovery - ignore few cached, out-of-date packets
        self.ignore_audio_packets = 0

        self.chunk_no = 0
        self.last_sender_chunk_no = None

    def init_queue(self):
        self.chunk_no = 0
        self.last_sender_chunk_no = None

    def do_recovery(self):
        "Flush the incoming, and probably stale, UDP buffer"
        # 60 == about 0.5s in default configuration
        self.ignore_audio_packets = 60
        self.last_sender_chunk_no = None
        self.chunk_no = 0
