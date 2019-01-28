from datetime import datetime, timedelta
import struct


class TimeMachine:
    """
    Handle fast millisecond precision timestamps

    `timemark' marks a time in future - certain number of milliseconds ahead of
    current time.
    """
    def get_timemark(self, latency):
        "Get a timemark `latency' ms in future"
        now = datetime.utcnow()
        stamp = (now.second * 1000) + (now.microsecond // 1000)
        # Stamp ranges from 0 to 59999 - fits in uint16_t
        stamp = (stamp + latency) % 59999
        stamp = struct.pack('>H', stamp)
        return now.timestamp() + latency/1000, stamp

    def to_absolute_timestamp(self, mark):
        now = datetime.utcnow()
        mark = struct.unpack('>H', mark)[0]
        second = mark // 1000
        microsecond = (mark % 1000) * 1000

        orig_now = now

        """
        Full cases in case time is not synchronised correctly in the network.
        ns           s
        now.second | second | solution     | Why
        0          | 1      | same minute  | s > ns, s-ns < 30
        0          | 59     | prev minute  | s > ns, s-ns > 30
        59         | 1      | next minute  | s < ns, ns-s > 30
        59         | 50     | same minute  | s < ns, ns-s < 30
        """

        if second > now.second:
            diff = second - now.second
            if diff < 30:
                # Same minute
                pass
            else:
                # Prev minute, won't be playing that I guess anyway
                now = now.replace(second=0, microsecond=0) + timedelta(minutes=-1)
        else:
            diff = now.second - second
            if diff > 30:
                # next minute
                now = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            else:
                # same minute
                pass

        absolute_mark = now.replace(second=second, microsecond=microsecond)

        return absolute_mark.timestamp()
