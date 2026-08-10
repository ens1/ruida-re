"""Incremental decoder for logical, unscrambled Ruida command streams."""

from __future__ import annotations

from .program import Record, decode_frame
from .registry import DEFAULT_REGISTRY
from .specs import CommandRegistry
from .syntax import is_command_start


class StreamDecoder:
    """Decode commands correctly even when input ends inside a command."""

    def __init__(self, registry: CommandRegistry = DEFAULT_REGISTRY):
        self.registry = registry
        self.buffer = bytearray()
        self.offset = 0
        self.issues: list[str] = []
        self._scan_index = 0

    def _decode(
        self,
        raw: bytes,
        offset: int,
        records: list[Record],
    ) -> None:
        record, issue = decode_frame(raw, offset, self.registry)
        records.append(record)
        if issue is not None:
            self.issues.append(issue)

    def feed(self, data: bytes, final: bool = False) -> list[Record]:
        self.buffer.extend(data)
        records: list[Record] = []
        if not self.buffer:
            return records
        scan_start = max(1, self._scan_index)
        frame_start = 0
        for index in range(scan_start, len(self.buffer)):
            if not is_command_start(self.buffer[index]):
                continue
            raw = bytes(self.buffer[frame_start:index])
            self._decode(raw, self.offset + frame_start, records)
            frame_start = index
        if final:
            raw = bytes(self.buffer[frame_start:])
            self._decode(raw, self.offset + frame_start, records)
            consumed = len(self.buffer)
        else:
            consumed = frame_start
        if consumed:
            del self.buffer[:consumed]
            self.offset += consumed
        self._scan_index = len(self.buffer)
        return records

    def finish(self) -> list[Record]:
        return self.feed(b"", final=True)
