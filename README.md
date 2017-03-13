Wavesync
========

The goal is achieving a perfectly synchronised multi-room playback over the local
area networks (Ethernet or Wi-Fi) using cheap components and no additional
cables. Main features:
- Receives a "combined" audio stream from PulseAudio over a Unix socket,
- chunks the audio, labels with timestamp and transmits over the network,
- then gathers audio on receivers and plays it using PyAudio (direct ALSA, or
  PulseAudio output).
- It's relatively trivial to setup and doesn't have exotic dependencies.

### Additionally

- Tested with RaspberryPI (Kodi and Mopidy players) and 4 receivers (rpi3
  (self), rpi2 with cheap-USB-WiFi, tethered Linux desktop, Linux laptop over
  Wifi).
- Works with multicast, unicast or broadcast transmission.
- Detects the silence and stops flooding the network.
- Works with Debian Stable/Raspbian Python 3 without compiling external
  dependencies (depends only on python3-pyaudio).
- Drops chunks gracefully to sync back when lagging behind.
- Requires NTP time synchronisation.
- Increases audio latency, not suitable for gaming and requires A-V correction
  for movie playback.

Configuration
-------------
0. Install at least:
  - python3
  - python3-pip
  - python3-pyaudio
  - pulseaudio (on sender)
  - ntp
  - ntpstat

1. Make sure NTP works correctly with ntpstat or ntptime:

  ```
  # ntpstat
  synchronised to NTP server (x.y.z.w) at stratum 3
     time correct to within 31 ms
     polling server every 1024 s
  ```

  Values 100-150ms seem to be OK with my setup. Anything much higher might cause
  problems. Crucial is the time difference between the receivers. < 20ms or
  better should be achievable locally.

2. Configure PulseAudio UNIX socket source on sender. For example:

  ```
  $ mkdir ~/.pulse; cd ~/.pulse
  $ cp /etc/pulse/default.pa .
  Add one line to the end of ~/.pulse/default.pa (without the backspace):
  load-module module-simple-protocol-unix rate=44100 format=s16le \
    channels=2 record=true source=0 socket=/tmp/music.source
  and restart PulseAudio:
  $ pulseaudio --kill && pulseaudio --start
  ```

  Refer to PA docs for details.

3. Install wavesync with git clone or pip3 install:

   ```
   pip3 install wavesync
   ```

4. Run sender:

  ```
  $ wavesync --tx /tmp/music.source
  ```

  You can use multiple --channel options, increase the total latency (--latency
  1500) or decrease the --payload-size. Define rate, channel number and sample
  size (16 bit/24 bit).

5. Run receivers:

  ```
  rpi-rx1 $ wavesync --rx
  rpi-rx2 $ wavesync --rx --sink-latency 700
  ```

  You can select output device with --device-index. Specify a --channel if using
  them on the sender.

6. Play music, fix your settings, try unicast in case of Wi-Fi, fine-tune
   sink-latency, observe latency drifts, check if NTP still works.

   If buffer underruns happen often - try increasing the buffer size
   (--buffer-size 16384).

   Extended example - transmitter with a multicast and two unicast receivers.
   ```
   # Transmitter and multicast-loopback receiver (rpi3 with USB DAC):
   tx-1 $ wavesync --tx /tmp/music.source --channel 224.0.0.57:45299 --channel 192.168.1.2:45299 --channel 192.168.1.3:45300
   tx-1 $ wavesync --rx --channel 224.0.0.57:45299

   # Cabled receiver:
   rx-1 $ wavesync --rx --channel 224.0.0.57:45299

   # RPI has a huge sink latency on built-in audio + needs unicast
   rx-rpiwifi $ wavesync --rx --channel 192.168.1.3:45300 --sink-latency=700
   # Laptop over wifi - needs unicast too.
   rx-laptop $ wavesync --rx --channel 192.168.1.2:45299
   ```

   Eliminate buffers on receivers - don't use PulseAudio there if not needed.

7. If you use this - drop me a note so I know it's useful. It might accidentally
   make me code something more or fix something. And I still have got few ideas.


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
                        |      PyAudio      |
                        |                   |
                 +------|------+     +------|------+
                 |             |     |             |
  sink latency   | PulseAudio  |     |     ALSA    |
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

About every 1s a status packet is sent with sender time, a number of total sent
packets and audio configuration. Receiver compares it to the packets received
after the previous status and calculates the number of network-dropped packets.

Tolerance
---------

```
       Tolerance     range
     /------------|------------\   Future
  ----------------X-------------|--------------------------->
                                |
                               NOW
  X = tolerance / 2
```

Wavesync gets the next chunk from the queue and calculates a delay between it's
desired play time and the current time.
- If the chunk play time is in future (delay > 1ms) - wait for it.
- If the chunk is in past:
  - delay between 0 and tolerance/2 - play,
  - delay between tolerance/2 and tolerance - drop with rising probability,
  - delay over tolerance - drop.

Packet format
-------------

```
  Byte:  [1 - 2][3   -   4][5         -       1420]
  Label: [Flags][Time Mark][RAW or compressed data]
```

Wavesync assumes network with MTU 1500 and optimistically small IP header
leading to a default payload size of 1472. It will try to autodetect MTU size
though and decrease this size automatically on start. You might want to decrease
it in some networks though (1500-60-20=1420 should be safe).

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
  Byte:  [3      -      11][12      -      16][     +20 bytes     ]
  Label: [Sender Timestamp][Total chunks sent][Audio Configuration]
  ```

  Audio configuration consists of:
  - rate (uint16_t)
  - sample size (uint8_t - value 16 or 24)
  - channels (uint8_t) - 1, 2 or pretty much any value
  - chunk_size (uint16_t)
  - system latency (uint_16_t)


Tips
----

1. Wi-Fi and multicast

  It doesn't work for me at all - neither with rpi+USB dongle nor a laptop with
  Intel Wifi card. Sometimes even broad/multicast transmission without
  a running receiver tends to observably increase the latencies of a ping of a
  wifi device (from 5ms to 180ms + introduces drops)

  Instead I use purely unicast transmission, but I believe combined multicast +
  unicast should work OK with a good access point.

2. How do I set sink-latency?

  If one device lags behind other consistently - increase it's sink-latency
  until you can't hear the lag. It's worse if the sink latency changes over
  time.

3. Why not use RTP pulseaudio module?

  Well, try it. It didn't worked for me with unicast addresses at all -
  hence, didn't worked over Wi-Fi. Also, from time to time, it was losing
  the sync over the cable even between tethered receivers.

4. How fast network do I need?

  For 2-channel, 16bit-sample, 44100Hz rate, 1500B (usual) network MTU the
  generated stream is around 121-125 packets per second - doesn't exceed
  1.5Mbit/s. Each additional unicast receiver (--channel option) increases
  the bandwidth.

  Compression is purely experimental option. It uses zlib compression instead of
  anything designed for audio (like FLAC). It will increase the CPU usage and
  might reduce the size of packets. Won't reduce their number though. The more
  unicast receivers the better impact of the compression.

5. If you're getting buffer underflows - try setting higher priority to wavesync
   using nice or try using rtkit. Didn't try it yet.

6. Packets currently are not reordered. If the network mangles the order of the
   packets - it won't work. On my LAN this doesn't happen.

7. RaspberryPI onboard sound card

  It's not very good. It should work though after some tweaking. Sink-latency
  can reach 1000ms (750ms for me) for it and because of huge buffering tends to
  "drift" over time. I haven't tried it after last rewrite of the code. Might've
  got better.

8. Nothing works!

  Check your firewall it might be dropping your packets. Check using tcpdump if
  the packets are reaching the receiver. Try unicast instead of multicast. If
  using multicast check ``ip maddr`` to see if shows your multicast group.

9. Still is not perfectly synced!

  Try reducing the output buffer size with --buffer-size 4096. Try flooding your
  home with water to increase the speed of sound.
