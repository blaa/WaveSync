import asyncio
import socket
import struct
import zlib

from libwavesync import Packetizer, AudioConfig
from libwavesync import time_machine

class Receiver(asyncio.DatagramProtocol):
    """
    Packet receiver

    - Receive packets
    - decode headers
    - store in chunk list.
    """

    def __init__(self, chunk_queue, channel, sink_latency_ms):
        # Store config
        self.channel = channel

        self.chunk_queue = chunk_queue

        self.stat_network_latency = 0
        self.stat_network_drops = 0

        # Audio configuration sent by transmitter
        self.audio_config = None
        self.sink_latency_ms = sink_latency_ms

        super().__init__()

    def connection_made(self, transport):
        "Configure multicast"
        sock = transport.get_extra_info('socket')
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Check if address is multicast and join group.
        group, port = self.channel

        multicast = True
        octets = group.split('.')

        # Received audio chunk counter
        self.chunk_queue.init_queue()

        if len(octets) != 4:
            multicast = False
        else:
            try:
                octet_0 = int(octets[0])
                if not 224 <= octet_0 <= 239:
                    multicast = False
            except ValueError:
                multicast = False

        # If not multicast - end
        if multicast is False:
            print("Assuming unicast reception on %s:%d" % (group, port))
            return

        # Multicast - join group
        print("Joining multicast group", group)

        group = socket.inet_aton(group)
        mreq = struct.pack('4sL', group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def _handle_status(self, data):

        if len(data) < (2 + 20):
            print("WARNING: Status header too short")

        (sender_timestamp,
         sender_chunk_no,
         rate, sample,
         channels,
         chunk_size,
         latency_ms) = struct.unpack('dIHBBHH',
                                     data[2:2 + 8+4+2+1+1+2+2])

        q = self.chunk_queue

        # Handle timestamp
        now = time_machine.now()
        self.stat_network_latency = (now - sender_timestamp)

        # Handle audio configuration
        audio_config = AudioConfig(rate, sample, channels,
                                   latency_ms,
                                   sink_latency_ms=self.sink_latency_ms)
        audio_config.chunk_size = chunk_size

        if audio_config != self.audio_config:
            # If changed - sent further
            q.chunk_list.append((q.CMD_CFG, audio_config))
            self.audio_config = audio_config

        # Handle dropped packets

        # If this is first status packet
        # or low sender_chunk_no indicates that sender was restarted
        if q.last_sender_chunk_no is None or sender_chunk_no < 1500:
            q.last_sender_chunk_no = sender_chunk_no
            q.chunk_no = 0
            return

        # How many chunks were transmitted since previous status packet?
        chunks_sent = sender_chunk_no - q.last_sender_chunk_no
        dropped = chunks_sent - q.chunk_no

        q.last_sender_chunk_no = sender_chunk_no
        q.chunk_no = 0

        self.stat_network_drops += dropped
        if dropped < 0:
            print("WARNING: More pkts received than sent! "
                  "You are receiving multiple streams or duplicates.")
        elif dropped > 0:
            q.chunk_list.append((q.CMD_DROPS, dropped))
            q.chunk_available.set()

    def datagram_received(self, data, addr):
        "Handle incoming datagram - audio chunk, or status packet"
        header = data[:2]
        mark = data[2:4]
        chunk = data[4:]
        if header == Packetizer.HEADER_RAW_AUDIO:
            pass
        elif header == Packetizer.HEADER_COMPRESSED_AUDIO:
            try:
                chunk = zlib.decompress(chunk)
            except zlib.error:
                print("WARNING: Invalid compressed data - dropping")
                return
        elif header == Packetizer.HEADER_STATUS:
            # Status header!
            self._handle_status(data)
            return
        else:
            print("Invalid header!")
            return

        if self.chunk_queue.ignore_audio_packets != 0:
            self.chunk_queue.ignore_audio_packets -= 1
            return

        mark = time_machine.to_absolute_timestamp(time_machine.now(),
                                                  mark)
        item = (mark, chunk)

        # Count received audio-chunks
        self.chunk_queue.chunk_no += 1

        self.chunk_queue.chunk_list.append((self.chunk_queue.CMD_AUDIO, item))
        self.chunk_queue.chunk_available.set()

    def error_received(self, exc):
        print('Error received:', exc)

    def connection_lost(self, exc):
        print("Socket closed, stop the event loop")
        loop = asyncio.get_event_loop()
        loop.stop()
