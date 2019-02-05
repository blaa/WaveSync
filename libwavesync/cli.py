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


def start_tx(args, loop, time_machine):
    "Initialize sender"

    # Transmitted configuration

    audio_config = AudioConfig(rate=args.audio_rate,
                               sample=24 if args.audio_sample else 16,
                               channels=args.audio_channels,
                               latency_s=args.latency_msec / 1000.0,
                               sink_latency_s=args.sink_latency_msec / 1000.0)

    audio_cfg = {
        'rate': args.audio_rate,
        'sample': 24 if args.audio_sample else 16,
        'channels': args.audio_channels,
        'latency_msec': args.latency_msec,
    }

    # Sound sample reader
    sample_reader = SampleReader()
    sample_reader.set_chunk_size(args.payload_size, args.sample_size)

    if args.local_play:
        chunk_queue = ChunkQueue()
        player = ChunkPlayer(chunk_queue,
                             receiver=None,
                             tolerance=args.tolerance_msec / 1000.0,
                             sink_latency=args.sink_latency_msec / 1000.0,
                             latency=args.latency_msec / 1000.0,
                             buffer_size=args.buffer_size,
                             device_index=args.device_index)
        play = player.chunk_player()
        asyncio.ensure_future(play)
    else:
        chunk_queue = None

    # Packet splitter / sender
    packetizer = Packetizer(sample_reader, time_machine,
                            chunk_queue,
                            args.latency_msec,
                            audio_cfg=audio_cfg,
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


def start_rx(args, loop, time_machine):
    "Initialize receiver"

    # Network receiver with it's connection
    channel = args.ip_list[0]
    chunk_queue = ChunkQueue()
    receiver = Receiver(chunk_queue,
                        time_machine,
                        channel=channel)

    connection = loop.create_datagram_endpoint(lambda: receiver,
                                               family=socket.AF_INET,
                                               local_addr=channel)

    # Coroutine pumping audio into PA
    player = ChunkPlayer(chunk_queue, receiver,
                         tolerance=args.tolerance_msec / 1000.0,
                         sink_latency=args.sink_latency_msec / 1000.0,
                         latency=args.latency_msec / 1000.0,
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
