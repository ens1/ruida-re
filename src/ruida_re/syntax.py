"""Lexical framing for logical, unscrambled Ruida streams."""

from __future__ import annotations

from collections.abc import Iterator


COMMAND_START = 0x80


def is_command_start(value: int) -> bool:
    return bool(value & COMMAND_START)


def next_frame_boundary(data: bytes) -> int | None:
    """Return the next boundary after the frame beginning at byte zero."""
    if not data:
        return None
    start = 1 if is_command_start(data[0]) else 0
    for index in range(start, len(data)):
        if is_command_start(data[index]):
            return index
    return None


def logical_frames(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield offsets and complete frames from a finite logical stream."""
    if not data:
        return
    frame_start = 0
    for index in range(1, len(data)):
        if is_command_start(data[index]):
            yield frame_start, data[frame_start:index]
            frame_start = index
    yield frame_start, data[frame_start:]
