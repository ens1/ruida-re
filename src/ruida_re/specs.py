"""Declarative Ruida command specifications."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from .fields import Field


NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
CONTROLLER_EFFECTS = frozenset(
    ("unknown", "read-only", "state-changing", "machine-action")
)
REPLY_BEHAVIORS = frozenset(("unknown", "none", "control", "data"))
SHAPE_EVIDENCE = frozenset(
    (
        "conflicting-reports",
        "external-fixture-observed",
        "fixture-observed",
        "hardware-observed",
        "reported",
        "simulator-only",
        "uncited-hypothesis",
    )
)
SEMANTIC_EVIDENCE = frozenset(
    (
        "controlled-fixture",
        "disputed",
        "hardware-observed",
        "partially-controlled",
        "reported",
        "uncited-hypothesis",
        "unverified",
    )
)


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
    controller_effect: str = "unknown"
    reply_behavior: str = "unknown"
    reply_commands: tuple[str, ...] = ()
    reply_field_matches: tuple[tuple[str, str], ...] = ()

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
        normalized = self.normalize_values(values)
        payload = b"".join(
            field.encode(normalized[field.name]) for field in self.fields
        )
        if any(value & 0x80 for value in payload):
            raise ValueError(
                f"Payload for {self.name} contains a command-start byte"
            )
        return self.opcode + payload

    def normalize_values(
        self,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Return values normalized to each field's JSON domain."""
        expected = {field.name for field in self.fields}
        actual = set(values)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"Fields for {self.name} do not match: "
                f"missing={missing}, extra={extra}"
            )
        return {
            field.name: field.normalize_json(values[field.name])
            for field in self.fields
        }


class CommandRegistry:
    """Lookup table for variable-length command opcodes."""

    def __init__(self, specs: Iterable[CommandSpec]):
        self._by_opcode: dict[bytes, list[CommandSpec]] = {}
        self._by_name: dict[str, CommandSpec] = {}
        self._specs: list[CommandSpec] = []
        for spec in specs:
            if not isinstance(spec.opcode, bytes):
                raise ValueError("Command opcode must be bytes")
            if (
                not isinstance(spec.name, str)
                or not NAME_PATTERN.fullmatch(spec.name)
            ):
                raise ValueError(f"Invalid command name {spec.name!r}")
            if spec.name in self._by_name:
                raise ValueError(f"Duplicate command name {spec.name}")
            if not all(isinstance(field, Field) for field in spec.fields):
                raise ValueError(
                    f"Command {spec.name} contains an invalid field"
                )
            field_names = [field.name for field in spec.fields]
            if any(
                not isinstance(name, str)
                or not NAME_PATTERN.fullmatch(name)
                for name in field_names
            ):
                raise ValueError(
                    f"Invalid field name in command {spec.name}"
                )
            if len(field_names) != len(set(field_names)):
                raise ValueError(
                    f"Duplicate field name in command {spec.name}"
                )
            if spec.shape_evidence not in SHAPE_EVIDENCE:
                raise ValueError(
                    f"Invalid shape evidence for {spec.name}: "
                    f"{spec.shape_evidence!r}"
                )
            if spec.semantic_evidence not in SEMANTIC_EVIDENCE:
                raise ValueError(
                    f"Invalid semantic evidence for {spec.name}: "
                    f"{spec.semantic_evidence!r}"
                )
            if spec.controller_effect not in CONTROLLER_EFFECTS:
                raise ValueError(
                    f"Invalid controller effect for {spec.name}: "
                    f"{spec.controller_effect!r}"
                )
            if spec.reply_behavior not in REPLY_BEHAVIORS:
                raise ValueError(
                    f"Invalid reply behavior for {spec.name}: "
                    f"{spec.reply_behavior!r}"
                )
            if any(
                not isinstance(name, str)
                or not NAME_PATTERN.fullmatch(name)
                for name in spec.reply_commands
            ):
                raise ValueError(
                    f"Invalid reply command for {spec.name}"
                )
            if len(spec.reply_commands) != len(set(spec.reply_commands)):
                raise ValueError(
                    f"Duplicate reply command for {spec.name}"
                )
            matches = spec.reply_field_matches
            if any(
                not isinstance(match, tuple)
                or len(match) != 2
                or any(
                    not isinstance(name, str)
                    or not NAME_PATTERN.fullmatch(name)
                    for name in match
                )
                for match in matches
            ):
                raise ValueError(
                    f"Invalid reply field match for {spec.name}"
                )
            if len(matches) != len(set(matches)):
                raise ValueError(
                    f"Duplicate reply field match for {spec.name}"
                )
            if matches and not spec.reply_commands:
                raise ValueError(
                    f"Reply field matches for {spec.name} need commands"
                )
            if (spec.reply_commands or matches) and (
                spec.reply_behavior != "data"
            ):
                raise ValueError(
                    f"Reply contract for {spec.name} requires data"
                )
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
