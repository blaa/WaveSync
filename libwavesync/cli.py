"""
Sender pipeline:
Unix socket/sound source
  --- samples stream ---> [Sample Reader]
  ---  sample_queue  ---> [Packetizer]
  ---> Uni/Multicast UDP

Receiver pipeline:
Socket
  ---  UDP datagrams  ---> [Receiver]
  --- chunks/commands ---> [ChunkPlayer]
  ---> pyaudio sink stream
"""

import asyncio
import socket

from . import (
    AudioConfig,
    Packetizer,
    ChunkPlayer,
    ChunkQueue,
    SampleReader,
    Receiver
)

from .cli_args import parse


def start_tx(args, loop):
    "Initialize sender"

    # Transmitted configuration
    audio_config = AudioConfig(rate=args.audio_rate,
                               sample=24 if args.audio_sample else 16,
                               channels=args.audio_channels,
                               latency_ms=args.latency_ms,
                               sink_latency_ms=args.sink_latency_ms)

    # Sound sample reader
    sample_reader = SampleReader(audio_config)
    sample_reader.payload_size = args.payload_size

    if args.local_play:
        chunk_queue = ChunkQueue()
        player = ChunkPlayer(chunk_queue,
                             receiver=None,
                             tolerance_ms=args.tolerance_ms,
                             buffer_size=args.buffer_size,
                             device_index=args.device_index)
        play = player.chunk_player()
        asyncio.ensure_future(play)
    else:
        chunk_queue = None

    # Packet splitter / sender
    packetizer = Packetizer(sample_reader,
                            chunk_queue,
                            audio_config,
                            compress=args.compress)

    packetizer.create_socket(args.ip_list,
                             args.ttl,
                             args.multicast_loop,
                             args.broadcast)

    connection = loop.create_unix_connection(lambda: sample_reader, args.tx)

    # Start loop
    asyncio.ensure_future(packetizer.packetize())
    asyncio.ensure_future(connection)
    loop.run_forever()


def start_rx(args, loop):
    "Initialize receiver"

    # Network receiver with it's connection
    channel = args.ip_list[0]
    chunk_queue = ChunkQueue()
    receiver = Receiver(chunk_queue,
                        channel=channel,
                        sink_latency_ms=args.sink_latency_ms)

    connection = loop.create_datagram_endpoint(lambda: receiver,
                                               family=socket.AF_INET,
                                               local_addr=channel)

    # Coroutine pumping audio into PA
    player = ChunkPlayer(chunk_queue, receiver,
                         tolerance_ms=args.tolerance_ms,
                         buffer_size=args.buffer_size,
                         device_index=args.device_index)

    play = player.chunk_player()

    tasks = asyncio.gather(connection, play)
    loop.run_until_complete(tasks)


def main():
    "Parse arguments and start the event loop"
    args = parse()

    loop = asyncio.get_event_loop()

    if args.debug:
        loop.set_debug(True)


    try:
        if args.tx is not None:
            start_tx(args, loop)
        elif args.rx:
            start_rx(args, loop)
    finally:
        loop.close()
