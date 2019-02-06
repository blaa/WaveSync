"""
Handle small, millisecond precision, timestamps.

`timemark' marks a time in future - certain number of milliseconds ahead of
the current time.
"""
from datetime import datetime
import struct

# Max latency which can be recorded. Can be limited by cli.
RANGE = 60


def get_timemark(relative_ts, latency_s):
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
    future_ts = relative_ts + latency_s
    stamp = int((future_ts % RANGE * 1000))
    mark = struct.pack('>H', stamp)
    return future_ts, mark


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
