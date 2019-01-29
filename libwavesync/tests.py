import asyncio
import unittest
from unittest.mock import Mock, MagicMock

from . import (
    TimeMachine,
    Packetizer,
    ChunkPlayer,
    ChunkQueue,
    SampleReader
)


async def mock_audio_generator(reader, packetizer, player):
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
    player.stop = True
    reader.data_received(sample)
    await asyncio.sleep(0)


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

    # Local play
    chunk_queue = ChunkQueue()
    player = ChunkPlayer(chunk_queue,
                         receiver=None,
                         tolerance=15 / 1000.0,
                         sink_latency=0,
                         latency=audio_cfg['latency_msec'] / 1000.0,
                         buffer_size=8192,
                         # Mock output device
                         device_index=-1)

    # Mock output 
    player._close_stream = Mock()
    player.stream = Mock()
    player.stream.get_write_available = Mock(return_value=300)
    player.stream.write = Mock()

    play = player.chunk_player()
    asyncio.ensure_future(play)

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
    task_reader = asyncio.ensure_future(mock_audio_generator(sample_reader,
                                                             packetizer,
                                                             player))

    # Start loop
    task_packetize = asyncio.ensure_future(packetizer.packetize())

    loop = asyncio.get_event_loop()
    tasks = asyncio.gather(task_reader, task_packetize, play)
    loop.run_until_complete(tasks)

    player.stream.write.assert_called()
    packetizer.sock.sendto.assert_called()



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

    def test_tx(self):
        mock_tx()
