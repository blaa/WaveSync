import sys
import asyncio
import unittest
from unittest.mock import Mock, MagicMock

from . import (
    TimeMachine,
    Packetizer,
    ChunkPlayer,
    ChunkQueue,
    SampleReader,
    Receiver,
    cli_args
)


async def mock_audio_generator(reader, packetizer, tx_player, rx_player):
    "Mock a unix socket, generate some 'audio' data."
    reader.connection_made(None)
    sample = b'0x01' * 6000

    # Generate "audio"
    for _ in range(0, 100):
        reader.data_received(sample)
        await asyncio.sleep(0)

    # Emulate audio underflow
    await asyncio.sleep(4)

    # Stop correctly pipeline
    print("mock_sample_finishing")

    packetizer.stop = True
    rx_player.stop = True
    tx_player.stop = True
    reader.data_received(sample)
    await asyncio.sleep(0)


def mock_chunk_player(latency_msec):
    "Mock chunk player"
    chunk_queue = ChunkQueue()
    player = ChunkPlayer(chunk_queue,
                         receiver=None,
                         tolerance=30 / 1000.0,
                         sink_latency=0,
                         latency=latency_msec / 1000.0,
                         buffer_size=8192,
                         # Mock output device
                         device_index=-1)

    # Mock output
    player._close_stream = Mock()
    original_open_stream = player._open_stream
    def open_stream():
        original_open_stream()

        # Open stream after _open_stream is called
        player.stream = Mock()
        player.stream.get_write_available = Mock(return_value=300)
        player.stream.write = Mock()

    player._open_stream = open_stream
    return chunk_queue, player


def mock_packetizer(audio_cfg, sample_reader, time_machine, chunk_queue):
    # Packet splitter / sender
    packetizer = Packetizer(sample_reader, time_machine,
                            chunk_queue,
                            audio_cfg['latency_msec'],
                            audio_cfg=audio_cfg,
                            compress=False)

    # Mock UDP socket
    packetizer.sock = Mock()
    packetizer.sock.sendto = Mock()
    packetizer.destinations = [("Mocked IP", 1234)]
    return packetizer


def mock_tx():
    """
    Mocked TX pipeline
    """
    # Transmitted configuration
    audio_cfg = {
        'rate': 44100,
        'sample': 24,
        'channels': 2,
        'latency_msec': 1000,
    }

    time_machine = TimeMachine()

    # Sound sample reader
    sample_reader = SampleReader()
    sample_reader.set_chunk_size(payload_size=1000, sample_size=4)

    chunk_queue, player = mock_chunk_player(audio_cfg['latency_msec'])

    # Packet splitter / sender
    packetizer = Packetizer(sample_reader, time_machine,
                            chunk_queue,
                            audio_cfg['latency_msec'],
                            audio_cfg=audio_cfg,
                            compress=False)

    # Mock UDP socket
    packetizer.sock = Mock()
    packetizer.sock.sendto = Mock()
    packetizer.destinations = [("Mocked IP", 1234)]

    # Mock audio input
    task_reader = mock_audio_generator(sample_reader,
                                       packetizer,
                                       player, player)

    # Start loop
    task_player = player.chunk_player()
    task_packetize = packetizer.packetize()

    loop = asyncio.get_event_loop()
    tasks = asyncio.gather(task_reader, task_packetize, task_player)
    loop.run_until_complete(tasks)

    player.stream.write.assert_called()
    packetizer.sock.sendto.assert_called()

    packets = [
        mc[1][0]
        for mc in packetizer.sock.sendto.mock_calls
    ]
    return packets


async def mock_packets(packets, receiver, player):
    "Mock a unix socket, generate some 'audio' data."

    transport = MagicMock()
    receiver.connection_made(transport)

    # Generate incoming "packets"
    for packet in packets:
        receiver.datagram_received(packet, "0.0.0.0")
        await asyncio.sleep(0)

    # Stop correctly pipeline
    print("mock_packets")

    player.stop = True
    await asyncio.sleep(0)


def mock_rx(packets):
    "Mocked RX pipeline"
    time_machine = TimeMachine()

    # Network receiver with it's connection
    channel = ('0.0.0.0', 1234)
    chunk_queue, player = mock_chunk_player(1000)

    receiver = Receiver(chunk_queue,
                        time_machine,
                        channel=channel)

    task_packets = mock_packets(packets, receiver, player)
    task_player = player.chunk_player()

    tasks = asyncio.gather(task_packets, task_player)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(tasks)


def mock_txrx():
    """
    Mocked TX-RX pipeline
    """
    # Transmitted configuration
    audio_cfg = {
        'rate': 44100,
        'sample': 24,
        'channels': 2,
        'latency_msec': 1000,
    }

    time_machine = TimeMachine()


    ##
    # Players
    ##
    rx_chunk_queue, rx_player = mock_chunk_player(audio_cfg['latency_msec'])
    tx_chunk_queue, tx_player = mock_chunk_player(audio_cfg['latency_msec'])

    ##
    # TX
    ##

    # Sound sample reader
    sample_reader = SampleReader()
    sample_reader.set_chunk_size(payload_size=1000, sample_size=4)

    tx_packetizer = mock_packetizer(audio_cfg, sample_reader,
                                    time_machine, tx_chunk_queue)

    # Mock audio input
    task_tx_reader = mock_audio_generator(sample_reader,
                                          tx_packetizer,
                                          tx_player,
                                          rx_player)

    ##
    # RX
    ##
    # Network receiver with it's connection
    channel = ('0.0.0.0', 1234)

    rx_receiver = Receiver(rx_chunk_queue,
                           time_machine,
                           channel=channel)

    # Combine TX-RX
    rx_receiver.connection_made(MagicMock())
    tx_packetizer.sock.sendto = rx_receiver.datagram_received

    ##
    # Start loop
    ##
    task_rx_player = rx_player.chunk_player()
    task_tx_player = tx_player.chunk_player()
    task_tx_packetize = tx_packetizer.packetize()

    loop = asyncio.get_event_loop()
    tasks = asyncio.gather(task_tx_reader,
                           task_tx_packetize,
                           task_tx_player,
                           task_rx_player)
    loop.run_until_complete(tasks)

    # Both played
    tx_player.stream.write.assert_called()
    rx_player.stream.write.assert_called()


class WaveSyncTestCase(unittest.TestCase):

    def test_timemachine(self):
        "Test timemark generation"
        time_machine = TimeMachine()

        def check(time):
            "Get timemark and convert it back. Check consistency"
            ts, mark = time_machine.get_timemark(time)
            ts_recovered = time_machine.to_absolute_timestamp(mark)
            diff = abs(ts - ts_recovered)
            return diff < 0.001

        times = [1000, 5000, 29000]
        for time in times:
            self.assertTrue(check(time))

        # Works up to 30s
        self.assertFalse(check(60000))

    #def test_pipelines(self):
    #    "Test TX-RX pipeline"
    #    packets = mock_tx()

    def test_pipelines(self):
        "Test TX-RX pipeline"
        mock_txrx()

    def test_arguments(self):
        "Test program argument parsing"
        with unittest.mock.patch.object(sys, 'argv', ['prog', '--rx']):
            cli_args.parse()
