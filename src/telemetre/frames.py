"""TF02-Pro serial frame parsing — pure, no I/O.

The Benewake TF02-Pro streams 9-byte frames continuously over UART at
115200 8N1 (factory default). Frame layout (little-endian):

    byte0  0x59        header
    byte1  0x59        header
    byte2  Dist_L      distance, low byte  (cm)
    byte3  Dist_H      distance, high byte
    byte4  Strength_L  signal strength, low byte
    byte5  Strength_H  signal strength, high byte
    byte6  Temp_L      chip temperature, low byte
    byte7  Temp_H      chip temperature, high byte
    byte8  Checksum    (sum(byte0..byte7)) & 0xFF

    distance_cm  = Dist_L  | Dist_H  << 8
    strength     = Str_L   | Str_H   << 8
    temperature  = (Temp_L | Temp_H  << 8) / 8 - 256   [deg C]

This module is deliberately dependency-free (stdlib only) so it can be
unit-tested off-hardware. See tests/test_frames.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional

HEADER = 0x59
FRAME_LEN = 9

# Reliability thresholds / sentinel values (from the TF02-Pro manual).
#   strength < 60         -> too weak, distance is unreliable (sensor emits 4500)
#   strength == 65535     -> receiver saturated (over-reflective target)
#   distance == 4500      -> "no/low target" sentinel, not a real 45 m reading
#   distance in {65534,65535} -> saturation / error
#   distance == 0         -> treat as invalid
MIN_STRENGTH = 60
STRENGTH_SATURATED = 65535
INVALID_DISTANCES = frozenset({0, 4500, 65534, 65535})

# Guard against unbounded growth if the port streams pure garbage.
_MAX_BUFFER = 4096


@dataclass(frozen=True)
class Frame:
    """A parsed TF02-Pro measurement."""

    distance_cm: int
    strength: int
    temp_raw: int

    @property
    def temperature_c(self) -> float:
        return self.temp_raw / 8.0 - 256.0

    @property
    def valid(self) -> bool:
        """True when the reading is trustworthy enough to display/use."""
        if self.strength < MIN_STRENGTH:
            return False
        if self.strength >= STRENGTH_SATURATED:
            return False
        if self.distance_cm in INVALID_DISTANCES:
            return False
        return True


def checksum(frame: bytes) -> int:
    """Lower 8 bits of the sum of the first 8 bytes."""
    return sum(frame[0:8]) & 0xFF


def valid_checksum(frame: bytes) -> bool:
    """True if `frame` is a well-formed 9-byte frame with a correct checksum."""
    if len(frame) < FRAME_LEN:
        return False
    if frame[0] != HEADER or frame[1] != HEADER:
        return False
    return frame[8] == checksum(frame)


def parse_frame(frame: bytes) -> Optional[Frame]:
    """Parse one 9-byte frame. Returns None on bad header/length/checksum."""
    if not valid_checksum(frame):
        return None
    distance = frame[2] | (frame[3] << 8)
    strength = frame[4] | (frame[5] << 8)
    temp_raw = frame[6] | (frame[7] << 8)
    return Frame(distance_cm=distance, strength=strength, temp_raw=temp_raw)


def find_frames(buffer: bytes) -> Iterator[Frame]:
    """Scan a byte buffer and yield every valid frame, skipping junk.

    Stateless helper used by tests and the standalone port sniffer. The live
    reader uses `FrameStreamParser` which retains a partial-frame tail between
    reads.
    """
    i = 0
    n = len(buffer)
    while i + FRAME_LEN <= n:
        if buffer[i] == HEADER and buffer[i + 1] == HEADER:
            candidate = buffer[i : i + FRAME_LEN]
            parsed = parse_frame(candidate)
            if parsed is not None:
                yield parsed
                i += FRAME_LEN
                continue
        i += 1  # resync: not a header, or header with a bad checksum


class FrameStreamParser:
    """Incremental parser for a live byte stream.

    Feed it whatever bytes arrive from the serial port; it buffers a partial
    tail across calls and returns the frames completed so far.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[Frame]:
        buf = self._buf
        buf.extend(data)
        out: List[Frame] = []
        i = 0
        n = len(buf)
        while i + FRAME_LEN <= n:
            if buf[i] == HEADER and buf[i + 1] == HEADER:
                parsed = parse_frame(bytes(buf[i : i + FRAME_LEN]))
                if parsed is not None:
                    out.append(parsed)
                    i += FRAME_LEN
                    continue
            i += 1
        del buf[:i]  # drop everything consumed/skipped, keep the partial tail
        if len(buf) > _MAX_BUFFER:  # runaway-garbage backstop
            del buf[: len(buf) - FRAME_LEN]
        return out

    def reset(self) -> None:
        self._buf.clear()
