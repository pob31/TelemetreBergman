#!/usr/bin/env python3
"""Standalone port sniffer — find which /dev/tty* the TF02-Pro is on.

    python3 scripts/detect_serial.py                 # scan default candidates
    python3 scripts/detect_serial.py /dev/ttySC0     # test one specific port

Reuses the pure frame parser in telemetre.frames, so a "valid frames" result
means real, checksum-passing TF02-Pro data (settles wiring/baud questions).
Needs pyserial (installed in the repo venv): .venv/bin/python scripts/detect_serial.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import serial  # pyserial

from telemetre.frames import find_frames

# Sensor is on the Serial Expansion HAT (I2C SC16IS752) channel B => ttySC1.
# NOTE: probing ttySC* is safe only with the matching *I2C* overlay loaded. With
# the wrong (SPI) overlay a phantom ttySC0 appears and close() dead-locks this
# sniffer in uninterruptible sleep — see the [serial] notes in config.py.
CANDIDATES = [
    "/dev/ttySC1", "/dev/ttySC0",
    "/dev/ttyAMA0", "/dev/serial0",
    "/dev/ttyUSB0", "/dev/ttyACM0",
]


def sniff(path, baud=115200, seconds=1.5):
    try:
        with serial.Serial(path, baud, timeout=0.3) as s:
            data = bytearray()
            t0 = time.time()
            while time.time() - t0 < seconds:
                data.extend(s.read(256))
        return len(data), list(find_frames(bytes(data)))
    except Exception as e:
        return None, e


def main():
    ports = sys.argv[1:] or CANDIDATES
    found = False
    for p in ports:
        nbytes, frames = sniff(p)
        if nbytes is None:
            print(f"{p:16} : cannot open ({frames})")
        elif frames:
            found = True
            f = frames[-1]
            print(
                f"{p:16} : {len(frames):3d} frames, {nbytes:4d}B  ->  "
                f"dist={f.distance_cm}cm  strength={f.strength}  "
                f"temp={f.temperature_c:.0f}C   [OK]"
            )
        else:
            print(f"{p:16} : {nbytes} bytes but NO valid frames (wrong wiring/baud/int_pin?)")
    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
