import unittest
from . import TimeMachine

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
