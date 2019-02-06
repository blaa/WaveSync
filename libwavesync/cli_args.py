import os
import argparse
from . import VERSION


def args_sender(snd):
    "Define TX options"
    snd.add_argument("--local-play",
                     action="store_true",
                     default=False,
                     help="play sound as well as transmitting it")

    snd.add_argument("--latency",
                     dest="latency_ms",
                     metavar="MSEC",
                     action="store",
                     default=1000,
                     type=int,
                     help="time to synchronise the outputs (default 1000).")

    snd.add_argument("--payload-size",
                     metavar="BYTES",
                     action="store",
                     type=int,
                     default=1472, # although 1500 - 80 would be safer.
                     help="UDP payload size, (default is 1472)")

    snd.add_argument("--ttl",
                     metavar="TTL",
                     action="store",
                     type=int,
                     default=2,
                     help="multicast TTL (default 2)")

    snd.add_argument("--compress",
                     metavar="LEVEL",
                     action="store",
                     default=False,
                     type=int,
                     help="enable compression (level 1-9)")

    snd.add_argument("--no-loop",
                     dest="multicast_loop",
                     action="store_false",
                     default=True,
                     help="Do not loop multicast packets back to the sender")

    snd.add_argument("--broadcast",
                     action="store_true",
                     help="Use broadcast transmission")

    snd.add_argument("--rate",
                     dest="audio_rate",
                     metavar="Hz",
                     action="store",
                     type=int,
                     default=44100,
                     help="Set player rate (default 44100Hz)")

    snd.add_argument("--24bits",
                     dest="audio_sample",
                     action="store_true",
                     help="24bit samples (default 16)")

    snd.add_argument("--channels",
                     dest="audio_channels",
                     action="store",
                     type=int,
                     default=2,
                     help="Set number of audio channels (default 2 - stereo)")


def args_receiver(rcv):
    "Define RX options"
    rcv.add_argument("--tolerance",
                     dest='tolerance_ms',
                     metavar="MSEC",
                     action="store",
                     type=int,
                     default=15,
                     help="play error tolerance (default 15ms)")

    rcv.add_argument("--sink-latency",
                     dest="sink_latency_ms",
                     metavar="MSEC",
                     action="store",
                     type=int,
                     default=0,
                     help="sink latency")

    rcv.add_argument("--buffer-size",
                     metavar="FRAMES",
                     action="store",
                     type=int,
                     default=8192,
                     help="size of local output buffer in frames (default 8192)")

    rcv.add_argument("--device-index",
                     metavar="NUMBER",
                     action="store",
                     type=int,
                     default=0,
                     help="audio device index for playback (default 0)")


def args_actions(act):
    "Define actions"
    act.add_argument("--tx",
                     metavar="INPUT",
                     action="store",
                     default=None,
                     help="transmit sound from a given unix socket")

    act.add_argument("--rx",
                     action="store_true",
                     default=False,
                     help="receive sound and play it")


def args_common(opt):
    "Define common options"
    opt.add_argument("--channel",
                     dest="ip_list",
                     metavar="ADDRESS:PORT",
                     action="append",
                     default=[],
                     help="multicast group or a unicast address, "
                          "may be given multiple times with --tx")

    opt.add_argument("--debug",
                     action="store_true",
                     help="enable debugging code")


def parse():
    "Parse program arguments"
    version = ".".join(str(p) for p in VERSION)

    dst = (
        "WaveSync %s - a multi-room sound synchronisation. "
        "https://github.com/blaa/WaveSync"
    )

    dst = dst % version
    parser = argparse.ArgumentParser(description=dst)
    snd = parser.add_argument_group('sender options')
    rcv = parser.add_argument_group('receiver options')
    opt = parser.add_argument_group('common')
    act = parser.add_argument_group('actions')

    args_sender(snd)
    args_receiver(rcv)
    args_actions(act)
    args_common(opt)

    args = parser.parse_args()

    if (args.tx is None) == (args.rx is False):
        parser.error('Exactly one action: --tx or --rx must be specified')

    if args.tx is not None:
        if not os.path.exists(args.tx):
            parser.error("--tx argument must point to a valid UNIX socket")


    if args.sink_latency_ms > args.latency_ms:
        parser.error("Sink latency cannot exceed system latency! Leave some margin too.")

    if args.latency_ms >= 5000:
        print("WARNING: You seem to be using large latency")
    elif args.latency_ms >= 29000:
        parser.error("Latency shouldn't exceed 29s (in fact, it should work with latency < 5000).")

    if args.device_index < 0:
        parser.error("Device index can't be negative")

    if not args.ip_list:
        args.ip_list.append('224.0.0.57:45300')

    if args.rx and len(args.ip_list) > 1:
        parser.error('Receiver must have only a single channel (IP)')

    # Parse IP addresses
    parsed_ip_list = []
    for arg in args.ip_list:
        tmp = arg.split(':')
        if len(tmp) != 2:
            parser.error('TX/RX channel not in format IP_ADDRESS:PORT: ' + arg)
        address, port = tmp

        try:
            port = int(port)
        except ValueError:
            parser.error('Port is not a number in channel: ' + arg)

        parsed_ip_list.append((address, port))
    args.ip_list = parsed_ip_list

    return args
