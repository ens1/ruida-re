"""Incremental decoder for logical, unscrambled Ruida command streams."""

from __future__ import annotations

from .program import Record, decode_frame
from .registry import DEFAULT_REGISTRY
from .specs import CommandRegistry
from .syntax import next_frame_boundary


class StreamDecoder:
    """Decode commands correctly even when input ends inside a command."""

    def __init__(self, registry: CommandRegistry = DEFAULT_REGISTRY):
        self.registry = registry
        self.buffer = bytearray()
        self.offset = 0
        self.issues: list[str] = []

    def feed(self, data: bytes, final: bool = False) -> list[Record]:
        self.buffer.extend(data)
        records: list[Record] = []
        while self.buffer:
            current = bytes(self.buffer)
            boundary = next_frame_boundary(current)
            if boundary is None and not final:
                break
            if boundary is None:
                boundary = len(current)
            raw = current[:boundary]
            record, issue = decode_frame(raw, self.offset, self.registry)
            records.append(record)
            if issue is not None:
                self.issues.append(issue)
            del self.buffer[:boundary]
            self.offset += boundary
        return records

    def finish(self) -> list[Record]:
        return self.feed(b"", final=True)
