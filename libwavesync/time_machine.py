from datetime import datetime, timedelta
import struct

RANGE = 60

def get_timemark(relative_ts, latency_ms):
    """
    Create a 1-ms resolution timemark equal to relative_ts + latency_ms.

    Args:
      relative_ts: UTC timestamp to which the mark will relate
      latency_ms: number of miliseconds in future
    Returns:
      16-bit binary marking the relative_ts + latency_ms time.
    """
    #base = int(relative // RANGE * RANGE)
    #mark = int((relative - (relative  // RANGE) * RANGE ) * 1000)
    ts = relative_ts + latency_ms / 1000
    stamp = int((ts % RANGE * 1000))
    mark = struct.pack('>H', stamp)
    return mark

def to_absolute_timestamp(relative_ts, mark):
    """
    Interpret a timemark as a full timestamp relative to `relative_ts`

    Args:
      relative_ts: UTC timestamp, close, but not exactly the same as the
                   one used when creating the mark.

      mark: 16-bit binary

    Returns:
      timestamp relative to relative_ts.
    """
    mark = struct.unpack('>H', mark)[0]
    base = relative_ts // RANGE * RANGE
    recovered = base + mark / 1000.0
    if recovered < relative_ts:
        # We ended up in the past, assume next interval
        recovered += RANGE
    return recovered

def now():
    "Current UTC timestamp"
    return datetime.utcnow().timestamp()

class TimeMachine:
    """
    Handle small, millisecond precision, timestamps.

    `timemark' marks a time in future - certain number of milliseconds ahead of
    the current time.
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
