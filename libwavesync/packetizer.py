"""
Packetizer class.
"""

import asyncio
import socket
import struct
import zlib

from datetime import datetime
from time import time

from libwavesync import time_machine

class Packetizer:
    """Read chunks from queue, add timestamp marks and send over multicast."""

    HEADER_COMPRESSED_AUDIO = b'\x80\x00'
    HEADER_RAW_AUDIO = b'\x00\x00'
    HEADER_STATUS = b'\x40\x00'

    def __init__(self, reader, chunk_queue, audio_config, compress=False):
        self.reader = reader
        self.chunk_queue = chunk_queue
        self.compress = compress
        self.audio_config = audio_config
        self.stop = False

        self.sock = None
        self.destinations = []

    def create_socket(self, channels, ttl, multicast_loop, broadcast):
        "Create a UDP multicast socket"
        self.sock = socket.socket(socket.AF_INET,
                                  socket.SOCK_DGRAM,
                                  socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP,
                             socket.IP_MULTICAST_TTL,
                             ttl)

        if multicast_loop is True:
            self.sock.setsockopt(socket.IPPROTO_IP,
                                 socket.IP_MULTICAST_LOOP, 1)

        if broadcast is True:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.destinations = [
            (address, port)
            for address, port in channels
        ]

        IP_PMTUDISC_DO = 2
        IP_MTU_DISCOVER = 10

        # Set DF flag on IP packet (Don't Fragment) - fragmenting would be bad idea
        # it's way better to chunk the packets right.
        self.sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER, IP_PMTUDISC_DO)

    def _create_status_packet(self, chunk_no):
        "Format status packet"
        flags = Packetizer.HEADER_STATUS
        now = datetime.utcnow().timestamp()
        dgram = flags + struct.pack('dIHBBHH',
                                    now,
                                    chunk_no,
                                    self.audio_config.rate,
                                    self.audio_config.sample,
                                    self.audio_config.channels,
                                    self.audio_config.chunk_size,
                                    self.audio_config.latency_ms)
        return dgram

    async def packetize(self):
        "Read pre-chunked samples from queue and send them over UDP"
        start = time()
        # Numer of sent packets
        stat_pkts = 0
        # Chunk number as seen by receivers
        chunk_no = 0
        bytes_sent = 0
        bytes_raw = 0
        cancelled_compressions = 0

        # Current speed measurement
        recent = 0
        recent_bytes = 0
        recent_start = time()

        # For local playback
        if self.chunk_queue is not None:
            self.chunk_queue.chunk_list.append((self.chunk_queue.CMD_CFG,
                                                self.audio_config))

        while not self.stop:
            # Block until samples are read by the reader.
            stream_time, chunk = await self.reader.get_next_chunk()

            # Handle input flood, to keep us within timemarking range.
            now = time_machine.now()
            diff = stream_time - now

            if diff > 0.5:
                print("Waiting to synchronize input stream. Stream-real, difference is",
                      diff)
                await asyncio.sleep(0.4)
            elif diff < -5:
                print("Input stream is lagging", diff)

            relative = stream_time
            future_ts, mark = time_machine.get_timemark(relative,
                                                        self.audio_config.latency_s)

            if self.chunk_queue is not None:
                item = (future_ts, chunk)
                self.chunk_queue.chunk_list.append((self.chunk_queue.CMD_AUDIO,
                                                    item))
                self.chunk_queue.chunk_available.set()

            chunk_len = len(chunk)
            if self.compress is not False:
                chunk_compressed = zlib.compress(chunk, self.compress)
                if len(chunk_compressed) < chunk_len:
                    # Go with compressed
                    dgram = Packetizer.HEADER_COMPRESSED_AUDIO + mark + chunk_compressed
                else:
                    # Cancel - compressed might not fit to packet
                    dgram = Packetizer.HEADER_RAW_AUDIO + mark + chunk
                    cancelled_compressions += 1
            else:
                dgram = Packetizer.HEADER_RAW_AUDIO + mark + chunk

            dgram_len = len(dgram)
            chunk_no += 1
            recent += 1
            for destination in self.destinations:
                try:
                    self.sock.sendto(dgram, destination)
                    bytes_sent += dgram_len
                    recent_bytes += dgram_len
                    bytes_raw += chunk_len + 4
                    stat_pkts += 1
                except OSError as ex:
                    import errno
                    if ex.errno == errno.EMSGSIZE:
                        s = "WARNING: UDP datagram size (%d) is too big for your network MTU"
                        s = s % len(dgram)
                        print(s)
                        new_size = self.reader.decrement_payload_size()
                        print("Trying MTU detection. New payload size is %d" % new_size)
                        break

            # Send small status datagram every 124 chunks - ~ 1 second
            # It's used to determine if some frames were lost on the network
            # and therefore if output buffer resync is required.
            # Contains the audio configuration too.
            if chunk_no % 124 == 0:
                dgram = self._create_status_packet(chunk_no)
                for destination in self.destinations:
                    self.sock.sendto(dgram, destination)

            if recent >= 100:
                # Main status line
                now = time()
                took_total = now - start
                took_recent = now - recent_start
                s = ("STATE: dsts=%d total: pkts=%d kB=%d time=%d "
                     "kB/s: avg=%.3f cur=%.3f")
                s = s % (
                    len(self.destinations),
                    stat_pkts,
                    bytes_sent / 1024, took_total,
                    bytes_sent / took_total / 1024,
                    recent_bytes / took_recent / 1024,
                )
                if self.compress:
                    s += ' compress_ratio=%.3f cancelled=%d'
                    s = s % (bytes_sent / bytes_raw, cancelled_compressions)
                print(s)

                recent_start = now
                recent_bytes = 0
                recent = 0
        print("- Packetizer stop")
