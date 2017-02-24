
Wavesync
========

The main goal is achieving perfectly synchronised multi-room playback over the local
area networks (Ethernet or Wi-Fi) using cheap component little cables:

- works as a network bridge between two PulseAudio instances,
- suitable for your RaspberryPI with Kodi/Mopidy/some other player,
- works over shaky wireless using cheap USB Wi-Fi adapters,
- works with Debian Stable/Raspbian Python with no external dependencies,
- works with multicast and unicast transmission,
- detects silence and stops flooding the network,
- requires NTP time synchronization,
- increases audio latency, not suitable for gaming and requires A-V correction
  for movie playback.


Configuration
-------------
0. Install at least:
  - python3
  - python3-pip
  - pulseaudio
  - ntp
  - ntpstat

1. Make sure NTP works correctly by calling ntpstat or ntptime:

    # ntpstat
    synchronised to NTP server (x.y.z.w) at stratum 3 
       time correct to within 31 ms
       polling server every 1024 s

  31ms should be fine. Anything over 100ms might cause problems. Crucial is the
  time on the receivers. < 20ms or better should be achievable within the LAN.
  
2. Configure PulseAudio UNIX socket source on sender. For example:
   
    $ mkdir ~/.pulse; cd ~/.pulse
    $ cp /etc/pulse/default.pa .
    $ echo 'load-module module-simple-protocol-unix rate=44100 \
            format=s16le channels=2 record=true \
            source=0 socket=/tmp/music.source' >> default.pa

3. Configure PulseAudio UNIX socket sink on receivers. For example:

    $ echo 'load-module module-simple-protocol-unix rate=44100 \
            format=s16le channels=2 playback=true sink=0 \
            socket=/tmp/music.sink' >> ~/.pulse/default.pa
           
  In both cases you can use /etc/pulse/default.pa, or system.pa if using
  PulseAudio in system mode. My sender is also a transmitter - I use two PA
  there, one on a user running a Mopidy which creates the unix source, and the
  second on another user which creates sink and uses the hardware sound card.
  This way Kodi for movies has direct access to soundcard and no additional
  latency.

4. Install wavesync with git clone or pip3 install.

5. Run sender:
  
    $ wavesync --tx /tmp/music.source 

6. Run receivers:

    rpi-rx1 $ wavesync --rx /tmp/music.sink --sink-latency 80
    rpi-rx2 $ wavesync --rx /tmp/music.sink --sink-latency 60

7. Play music, fix your settings, try unicast in case of Wi-Fi, fine-tune
   sink-latency, observe latency drifts, check if NTP still works. 

8. If you use this - drop me a note so I know it's useful. It might accidentally
   make me code something more or fix something. And I've got few ideas.


Architecture
------------

```
  rpi-tx:
   +---------+       +------------+
   |         |       |            |   Unix socket
   | Player  +-------> PulseAudio +---------+
   |         |       |            |         |
   +---------+       +------------+         |
                                     +------|------+
                                     |             | split stream into
  system latency        +------------+ WaveSync TX | into packets and
                        |            |             | mark with future time
                        |            +------|------+
                        |                   |
                      Wi-Fi              Ethernet
                        |                   |
             rpi-rx1:   |        rpi-rx2:   |
                 +------|------+     +------|------+
                 |             |     |             | reassemble stream,
  tolerance      | WaveSync RX |     | WaveSync RX | buffer sound until
                 |             |     |             | the marked time
                 +------|------+     +------|------+
                        |  unix sockets     |
                        |                   |
                 +------|------+     +------|------+
                 |             |     |             |
  sink latency   | PulseAudio  |     | PulseAudio  |
                 |             |     |             |
                 +-------------+     +-------------+
```

Sender marks audio chunks with a time equal to ``current sender time + system
latency`` and transmits them. Receivers buffer the chunks and wait until their
current time equals ``chunk time - sink latency``. Sink latency can be set
differently on each receivers and allows to fine-tune the audio for different
devices. If the chunk time is missed by more than ``tolerance`` (in case of
network glitches or too slow sink) the chunks are dropped to get back to sync.

To be honest wavesync doesn't care what it forwards. It takes it from unix pipes
and puts somewhere else into some other unix pipes in a synchronised way.


Packet format
-------------

    Byte:  [1 - 2][3   -   4][5         -       1420]
    Label: [Flags][Time Mark][RAW or compressed data]

For a given medium maximal possible packets are transmitted. 80 bytes are
subtracted from MTU to fit IP (assuming pessimistically large header) and UDP
headers. 

Flags come `free' because a generic 2 channel, 16 bit audio requires 4
bytes for 1 complete sample. Timemark is required and 1420-2=1418 doesn't
divide by 4 - so we can transport 1416 bytes of sample data at once.

Flags: 
12345678ABCDEFGH
Bit 1: 0 - not compressed, 1 - compressed
Rest: Reserved :) A-H will probably extend timemark to cover one hour.


Tips
----

1. Wi-Fi and multicast

  It doesn't work for me at all with my USB dongle. Instead I trasmit to a
  multicast group for cable receivers and additionally to a unicast IP to reach
  Wi-Fi device with two options: --ip 224.0.0.56 --ip 192.168.1.10
  
  Make sure your unicast receiver doesn't get two streams instead of one.

2. How do I set sink-latency? 

  If one device lags behind other ones - increase it's sink-latency until you're
  fine with it. It's worse if the sink latency changes over time. You can get an
  estimate from the PulseAudio debug output. For e.g.:

    (...)Using 1.0 fragments of size 65536 bytes (371.52ms), 
    buffer size is 65536 bytes (371.52ms)
    
    Using 4.0 fragments of size 4408 bytes (24.99ms), buffer size is 17632 bytes
    (99.95ms)
    
  But you're interested in the differences between the sinks, and not absolute
  values.

3. It's easy to create an audio loop over the network. Don't be surprised if it
   happens and fix your configuration.

4. How fast network do I need?

  For 2 channel, 16bit sample, 44100Hz rate, 1500B (usual) network MTU the
  generated stream is around 125 packets per second - doesn't exceed 1.5Mbit/s.
  Each additional unicast receiver (--ip option) will multiply this result.
  
  Using compression will increase CPU usage and might reduce the size of
  packets. Won't reduce their number. Might help you in some cases but it's
  there for experiments.

5. You should probably use rtkit and make PA and probably this code realtime as
   much as possible. Didn't tried it yet.

6. Packets currently are not reordered. If the network mangles the order of the
   packets - it won't work. On LAN it should be OK. If needed it's a certain
   item for a TODO list.

7. Nothing works!

  Check your firewall it might be dropping your packets. Check using tcpdump if
  the packets are reaching the receiver. Try unicast instead of multicast. If
  using multicast check ``ip maddr`` to see if shows your multicast group.

8. Still is not perfectly synced!

  Try flooding your home with water to increase the speed of sound.
