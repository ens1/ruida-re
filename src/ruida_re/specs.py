"""Declarative Ruida command specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .fields import Field


@dataclass(frozen=True)
class CommandSpec:
    """Opcode, name, and ordered fields for one Ruida command."""

    opcode: bytes
    name: str
    fields: tuple[Field, ...] = ()
    shape_evidence: str = "reported"
    semantic_evidence: str = "reported"
    shape_sources: tuple[str, ...] = ()
    semantic_sources: tuple[str, ...] = ()
    notes: str = ""

    def decode(self, data: bytes, offset: int) -> tuple[dict[str, Any], int]:
        if data[offset : offset + len(self.opcode)] != self.opcode:
            raise ValueError(
                f"Expected opcode {self.opcode.hex()} at {offset}"
            )
        cursor = offset + len(self.opcode)
        values: dict[str, Any] = {}
        for field in self.fields:
            value, cursor = field.decode(data, cursor)
            values[field.name] = value
        return values, cursor

    def encode(self, values: dict[str, Any]) -> bytes:
        expected = {field.name for field in self.fields}
        actual = set(values)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"Fields for {self.name} do not match: "
                f"missing={missing}, extra={extra}"
            )
        payload = b"".join(
            field.encode(values[field.name]) for field in self.fields
        )
        if any(value & 0x80 for value in payload):
            raise ValueError(
                f"Payload for {self.name} contains a command-start byte"
            )
        return self.opcode + payload


class CommandRegistry:
    """Lookup table for variable-length command opcodes."""

    def __init__(self, specs: Iterable[CommandSpec]):
        self._by_opcode: dict[bytes, list[CommandSpec]] = {}
        self._by_name: dict[str, CommandSpec] = {}
        self._specs: list[CommandSpec] = []
        for spec in specs:
            if spec.name in self._by_name:
                raise ValueError(f"Duplicate command name {spec.name}")
            if not spec.opcode or not spec.opcode[0] & 0x80:
                raise ValueError(
                    f"Opcode must start with a high-bit byte: "
                    f"{spec.opcode.hex()}"
                )
            if any(value & 0x80 for value in spec.opcode[1:]):
                raise ValueError(
                    f"Opcode suffix must use seven-bit bytes: "
                    f"{spec.opcode.hex()}"
                )
            self._by_opcode.setdefault(spec.opcode, []).append(spec)
            self._by_name[spec.name] = spec
            self._specs.append(spec)
        self._widths = sorted(
            {len(opcode) for opcode in self._by_opcode},
            reverse=True,
        )

    def match(self, data: bytes, offset: int) -> CommandSpec | None:
        candidates = self.candidates(data, offset)
        return candidates[0] if candidates else None

    def candidates(
        self,
        data: bytes,
        offset: int = 0,
    ) -> list[CommandSpec]:
        """Return every shape whose opcode matches, longest first."""
        result = []
        remaining = len(data) - offset
        for width in self._widths:
            if width > remaining:
                continue
            result.extend(
                self._by_opcode.get(data[offset : offset + width], ())
            )
        return result

    def could_match_prefix(self, data: bytes, offset: int = 0) -> bool:
        fragment = data[offset:]
        if not fragment:
            return False
        return any(opcode.startswith(fragment) for opcode in self._by_opcode)

    def opcode(self, value: bytes) -> CommandSpec | None:
        specs = self._by_opcode.get(value, ())
        return specs[0] if len(specs) == 1 else None

    def name(self, value: str) -> CommandSpec | None:
        return self._by_name.get(value)

    def __iter__(self):
        return iter(self._specs)
