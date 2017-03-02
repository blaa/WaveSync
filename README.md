Wavesync
========

The main goal is achieving perfectly synchronised multi-room playback over the local
area networks (Ethernet or Wi-Fi) using cheap components and no additional cables:

- works as a network bridge between two PulseAudio instances,
- suitable for your RaspberryPI with Kodi/Mopidy/some other player,
  although USB soundcard or HDMI output is recommended.
- works over shaky wireless using cheap USB Wi-Fi adapters,
- works with Debian Stable/Raspbian Python with no external dependencies,
- works with multicast, unicast or broadcast transmission,
- detects silence and stops flooding the network,
- generates stable "pressure" on output buffers to get rid of sink latency variation,
- uses graceful probabilistic packet drop to sync when lagging behind,
- inserts intermittent silence chunks on packet drops to gracefully sync playback,
- requires NTP time synchronization,
- increases audio latency, not suitable for gaming and requires A-V correction
  for movie playback.

Configuration
-------------
0. Install at least:
  - python3
  - python3-pip
  - pulseaudio (on sender)
  - portaudio19-dev (on receiver + pip3 install pyaudio or just python-pyaudio if not using pip3)
  - ntp
  - ntpstat

1. Make sure NTP works correctly by calling ntpstat or ntptime:

  ```
  # ntpstat
  synchronised to NTP server (x.y.z.w) at stratum 3
     time correct to within 31 ms
     polling server every 1024 s
  ```
  
  30-50ms should be fine. Anything over 100ms might cause problems. Crucial
  is the time on the receivers. < 20ms or better should be achievable
  locally, but seems to work with 58ms on one receiver too.


2. Configure PulseAudio UNIX socket source on sender. For example:

  ```
  $ mkdir ~/.pulse; cd ~/.pulse
  $ cp /etc/pulse/default.pa .
  $ echo 'load-module module-simple-protocol-unix rate=44100 \
          format=s16le channels=2 record=true \
          source=0 socket=/tmp/music.source' >> default.pa
  ```

3. Configure PulseAudio UNIX socket sink on receivers. For example:

  ```
  $ echo 'load-module module-simple-protocol-unix rate=44100 \
          format=s16le channels=2 playback=true sink=0 \
          socket=/tmp/music.sink' >> ~/.pulse/default.pa
  ```

  In both cases you can use /etc/pulse/default.pa, or system.pa if using
  PulseAudio in system mode. My sender is also a transmitter - I use two PA
  there, one on a user running a Mopidy which creates the unix source, and
  the second on another user which creates sink and uses the hardware sound
  card. This way Kodi for movies has a direct access to the soundcard and
  no additional latency.

4. Install wavesync with git clone or pip3 install: ```pip3 install
   wavesync````

5. Run sender:

  ```
  $ wavesync --tx /tmp/music.source
  ```

6. Run receivers:

  ```
  rpi-rx1 $ wavesync --rx /tmp/music.sink --sink-latency 80
  rpi-rx2 $ wavesync --rx /tmp/music.sink --sink-latency 700
  ```

7. Play music, fix your settings, try unicast in case of Wi-Fi, fine-tune
   sink-latency, observe latency drifts, check if NTP still works.
   
   Extended example - transmitter with a multicast and two unicast receivers.
   ```
   # Transmitter and multicast-loopback receiver (rpi3 with USB DAC):
   tx-1 $ wavesync --tx /tmp/music.source --channel 224.0.0.57:45299 --channel 192.168.1.2:45299 --channel 192.168.1.3:45300
   tx-1 $ wavesync --rx /tmp/music.sink --channel 224.0.0.57:45299
   
   # Cabled receiver:
   rx-1 $ wavesync --rx /tmp/music.sink --channel 224.0.0.57:45299
   
   # RPI has a huge sink latency on built-in audio + needs unicast
   rx-rpiwifi $ wavesync --rx /tmp/music.sink --channel 192.168.1.3:45300 --sink-latency=700
   # Laptop over wifi - needs unicast too.
   rx-laptop $ wavesync --rx /tmp/music.sink --channel 192.168.1.2:45299 
   ```

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

Sender marks audio chunks with a time equal to ``current sender time +
system latency`` and transmits them. Receivers buffer the chunks and wait
until their current time equals ``chunk time - sink latency``. Sink latency
can be set differently on each receivers and allows to fine-tune the audio
for different devices. If the chunk time is missed by more than
``tolerance`` (in case of a too slow sink) the chunks are dropped to get back
in sync. 

About every 1s a status packet is sent with sender time and number of total
sent packets. Receiver compares it to the packets received after the
previous status and calculates the number of network-dropped packets. For
each dropped chunk a silence chunk is generated to synchronise the output.

Wavesync didn't care about "chunk" content in the beginning - it took data
from unix pipe and put into some other pipes somewhere else in a
synchronised manner. After it started inserting silence it's no longer
true.


Packet format
-------------

```
  Byte:  [1 - 2][3   -   4][5         -       1420]
  Label: [Flags][Time Mark][RAW or compressed data]
```

By default wavesync assumes network with MTU 1500 and pessimistically large
IP header (60 bytes) and hence starts with a payload size of 1420. 
Payload usually can be increased to get lower pkt/s.

Flags come `free' because a generic 2-channel, 16-bit audio requires 4
bytes for a 1 complete sample. Marking with time is required, but
1420-2=1418 doesn't divide by 4 - so we can transport 1416 bytes of sample
data at once.

RAW data can be anything, but usually it's a sample stream: 16 bit values
for left/right channels. Wavesync assumes silence is coded as zeroes.

Flags:
12345678ABCDEFGH

- Bit 1: 0 - not compressed, 1 - compressed
- Bit 2: 0 - audio frame, 1 - status frame

  ```
  Byte:  [3      -      11][12      -      16]
  Label: [Sender Timestamp][Total chunks sent]
  ```           

- Rest: Reserved :)


Tips
----

1. Wi-Fi and multicast

  It doesn't work for me at all - neither with rpi+USB dongle nor laptop
  with Intel Wifi card. Instead I trasmit to a multicast group for cable
  receivers and additionally to a unicast IP to reach Wi-Fi device with
  multiple --channel options.

  Make sure your unicast receiver doesn't get two streams instead of one.
  It should tell you if it detects it.

2. How do I set sink-latency?

  If one device lags behind other consistently - increase it's sink-latency
  until you're fine with it. It's worse if the sink latency changes over
  time. You can get an estimate from the PulseAudio debug output. For e.g.:

  ```
  (...)Using 1.0 fragments of size 65536 bytes (371.52ms),
  buffer size is 65536 bytes (371.52ms)

  Using 4.0 fragments of size 4408 bytes (24.99ms), buffer size is 17632 bytes
  (99.95ms)
  
  protocol-native.c: Final latency 200.00 ms = 90.00 ms + 2*10.00 ms + 90.00 ms
  ```
  
  But you're interested in the differences between the sinks, and not absolute
  values.

3. Why not use RTP pulseaudio module?

  Well, try it. It didn't worked for me with unicast addresses at all -
  hence, didn't worked over Wi-Fi. Also, from time to time, it was losing
  the sync over the cable too.

4. How fast network do I need?

  For 2-channel, 16bit-sample, 44100Hz rate, 1500B (usual) network MTU the
  generated stream is around 125 packets per second - doesn't exceed 1.5Mbit/s.
  Each additional unicast receiver (--channel option) will increase it.

  Using compression will increase CPU usage and might reduce the size of
  packets. Won't reduce their number. Might help you in some cases but it's
  there for experiments. The more unicast receivers the better the
  compression.

5. You should probably use rtkit or high system priorities. Didn't tried it
   yet. 

   In daemon.conf try:
   ```
   realtime-scheduling = yes
   realtime-priority = 5
   ```
   And check debug output to see if it works.

6. Packets currently are not reordered. If the network mangles the order of the
   packets - it won't work. On my LAN this doesn't happen. 

7. RaspberryPI onboard sound card

  It's not very good. It should work though after some tweaking.
  Sink-latency can reach 1000ms (750ms for me) for it and because of huge buffering tends
  to "drift" over time.
  
  Try setting tsched=0 and decreasing number of default-fragments and their
  size in daemon.conf. Try:
  ```echo performance | tee /sys/devices/system/cpu/*/cpufreq/scaling_governor```

8. Nothing works!

  Check your firewall it might be dropping your packets. Check using tcpdump if
  the packets are reaching the receiver. Try unicast instead of multicast. If
  using multicast check ``ip maddr`` to see if shows your multicast group.

9. Still is not perfectly synced!

  Try flooding your home with water to increase the speed of sound.
