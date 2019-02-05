import sys
import asyncio
import unittest
from datetime import datetime
from unittest.mock import Mock, MagicMock

from . import (
    AudioConfig,
    Packetizer,
    ChunkPlayer,
    ChunkQueue,
    SampleReader,
    Receiver,
    cli_args,
)

from . import time_machine as tm


async def mock_audio_generator(reader, packetizer, tx_player, rx_player):
    "Mock a unix socket, generate some 'audio' data."
    reader.connection_made(None)
    sample = b'\x01\x02'
    frame = 2 * sample
    pre_chunk = frame * 300

    # Generate "audio"
    for _ in range(0, 2000):
        reader.data_received(pre_chunk)
        await asyncio.sleep(0)

    # Emulate audio underflow
    await asyncio.sleep(4)

    # Stop correctly pipeline
    print("mock_sample_finishing")

    print("Closing RX Player")
    rx_player.stop = True
    for _ in range(0, 200):
        reader.data_received(pre_chunk)
        await asyncio.sleep(0)

    print("Closing TX Player and Packetizer")
    tx_player.stop = True
    packetizer.stop = True
    for _ in range(0, 10):
        reader.data_received(pre_chunk)
        await asyncio.sleep(0)

def mock_chunk_player():
    "Mock chunk player"
    chunk_queue = ChunkQueue()
    player = ChunkPlayer(chunk_queue,
                         receiver=None,
                         tolerance=30 / 1000.0,
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


def mock_packetizer(audio_config, sample_reader, time_machine, chunk_queue):
    # Packet splitter / sender
    packetizer = Packetizer(sample_reader, time_machine,
                            chunk_queue,
                            audio_config,
                            compress=False)

    # Mock UDP socket
    packetizer.sock = Mock()
    packetizer.sock.sendto = Mock()
    packetizer.destinations = [("Mocked IP", 1234)]
    return packetizer


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


def mock_txrx():
    """
    Mocked TX-RX pipeline
    """

   # Transmitted configuration
    audio_config = AudioConfig(rate=44100,
                               sample=16,
                               channels=2,
                               latency_ms=1000,
                               sink_latency_ms=0)

    # # Transmitted configuration
    # audio_cfg = {
    #     'rate': 44100,
    #     'sample': 24,
    #     'channels': 2,
    #     'latency_msec': 1000,
    # }

    time_machine = tm.TimeMachine()

    ##
    # Players
    ##
    rx_chunk_queue, rx_player = mock_chunk_player()
    tx_chunk_queue, tx_player = mock_chunk_player()

    ##
    # TX
    ##

    # Sound sample reader
    sample_reader = SampleReader(audio_config)
    sample_reader.payload_size = 1000

    tx_packetizer = mock_packetizer(audio_config, sample_reader,
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
                           channel=channel,
                           sink_latency_ms=0)

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

    # TODO recheck after fixing time stream
    assert rx_player.stream.write.call_count >= 1
    assert tx_player.stream.write.call_count > 1000

class WaveSyncTestCase(unittest.TestCase):

    def test_timemachine(self):
        "Test timemark generation"
        time_machine = tm.TimeMachine()

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

    def test_new_timemachine(self):
        "Test timemark generation"
        def check(relative1, relative2, latency_ms):
            """
            Get timemark and convert it back to check consistency.

            relative1 - time during tm creation
            relative2 - time during ts extraction
            """
            mark = tm.get_timemark(relative1, latency_ms)
            ts_recovered = tm.to_absolute_timestamp(relative2, mark)
            diff = abs(relative1 + latency_ms/1000 - ts_recovered)
            return diff < 0.001

        relatives = [
            # Exact interval
            1549305460.0,

            # Last moment of previous
            1549305459.0,

            # Next chunk
            1549305510.0
        ]
        times = [2000, 5000, 25000]
        for relative in relatives:
            for time in times:
                print("rel", relative, "time", time)
                self.assertTrue(check(relative, relative-3, time))
                self.assertTrue(check(relative, relative+0.2, time))
                self.assertTrue(check(relative, relative+0.9, time))
                self.assertTrue(check(relative, relative+1.8, time))

        # Won't work, will assume next interval
        self.assertFalse(check(relatives[0], relatives[0]+10, 3000))


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
