#!/usr/bin/env python3

from distutils.core import setup

VERSION=(0, 1, 0)

setup(name="wavesync",
      version=".".join(str(f) for f in VERSION),
      description="Multi-room synchronised audio playback",
      author="Tomasz bla Fortuna",
      author_email="bla@thera.be",
      url="https://github.com/blaa/WaveSync",
      keywords="multi-room synchronised audio playback raspberrypi pulseaudio",
      scripts=['wavesync'],
      license="MIT",
      classifiers=[
          "Development Status :: 4 - Beta",
          "Topic :: Multimedia :: Sound/Audio",
          "Topic :: System :: Networking",
          "License :: OSI Approved :: MIT License",
      ],

)

