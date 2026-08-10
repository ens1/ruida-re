"""Lossless Ruida file decoding, editing, and encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeAlias

from .codec import swizzle, unswizzle
from .fields import FieldError
from .jsonio import integer as json_integer
from .jsonio import loads as load_json
from .jsonio import number as json_number
from .registry import get_registry
from .specs import CommandRegistry, NAME_PATTERN
from .syntax import is_command_start, logical_frames
from .transport import decode_datagram, encode_datagram


SCHEMA = "ruida-re.program.v1"
CONTAINERS = ("rd", "udp", "logical")
SHAPE_EVIDENCE = {
    "conflicting-reports",
    "external-fixture-observed",
    "fixture-observed",
    "reported",
    "simulator-only",
    "uncited-hypothesis",
}
SEMANTIC_EVIDENCE = {
    "controlled-fixture",
    "disputed",
    "partially-controlled",
    "reported",
    "uncited-hypothesis",
    "unverified",
}


def _hex_bytes(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a hexadecimal string")
    if (
        len(value) % 2
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must use canonical lowercase hexadecimal")
    try:
        return bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"{label} must be hexadecimal") from error


def _offset(value: Any) -> int:
    return json_integer(value, "Record offset", minimum=0)


@dataclass
class KnownCommand:
    """A command with a registry specification and decoded values."""

    offset: int
    opcode: str
    name: str
    values: dict[str, Any]
    raw: str | None = None
    shape_evidence: str = "reported"
    semantic_evidence: str = "reported"
    _raw_fallback_values: dict[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def encode(self, registry: CommandRegistry) -> bytes:
        self.offset = _offset(self.offset)
        opcode = _hex_bytes(self.opcode, "Command opcode")
        if (
            not opcode
            or not is_command_start(opcode[0])
            or any(is_command_start(value) for value in opcode[1:])
        ):
            raise ValueError("Command opcode does not follow frame grammar")
        if (
            not isinstance(self.name, str)
            or not NAME_PATTERN.fullmatch(self.name)
        ):
            raise ValueError("Command name must be a stable snake-case name")
        if not isinstance(self.values, dict):
            raise ValueError("Command values must be a mapping")
        for name in self.values:
            if not isinstance(name, str):
                raise ValueError("Command field names must be strings")
        if self.shape_evidence not in SHAPE_EVIDENCE:
            raise ValueError(
                f"Unknown shape evidence: {self.shape_evidence!r}"
            )
        if self.semantic_evidence not in SEMANTIC_EVIDENCE:
            raise ValueError(
                f"Unknown semantic evidence: {self.semantic_evidence!r}"
            )
        spec = registry.name(self.name)
        if spec is None:
            normalized_values: dict[str, Any] = {}
            for name, value in self.values.items():
                if isinstance(value, str):
                    normalized_values[name] = value
                elif isinstance(value, bool) or not isinstance(
                    value,
                    (int, float, Decimal),
                ):
                    raise ValueError(
                        "Command values must be JSON numbers or strings"
                    )
                else:
                    normalized_values[name] = json_number(
                        value,
                        f"Command field {name}",
                    )
            self.values = normalized_values
            if self.raw is None:
                raise ValueError(
                    f"Unknown structured command without raw bytes: "
                    f"{self.name}"
                )
            raw = _hex_bytes(self.raw, "Command raw value")
            if not raw or raw[: len(opcode)] != opcode:
                raise ValueError(
                    f"Unknown command {self.name} raw bytes do not "
                    "match its opcode"
                )
            if any(is_command_start(value) for value in raw[1:]):
                raise ValueError(
                    f"Unknown command {self.name} raw bytes contain "
                    "another frame"
                )
            if self._raw_fallback_values is None:
                self._raw_fallback_values = dict(self.values)
            elif self.values != self._raw_fallback_values:
                raise ValueError(
                    f"Cannot edit unknown command {self.name} without "
                    "a registry specification"
                )
            return raw
        self.values = spec.normalize_values(self.values)
        if opcode != spec.opcode:
            raise ValueError(
                f"Command {self.name} uses opcode {spec.opcode.hex()}, "
                f"not {self.opcode}"
            )
        encoded = spec.encode(self.values)
        if self.raw is not None:
            raw = _hex_bytes(self.raw, "Command raw value")
            try:
                original, end = spec.decode(raw, 0)
            except (FieldError, ValueError):
                original = None
                end = -1
            if end == len(raw) and original == self.values:
                return raw
        return encoded

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": "command",
            "offset": self.offset,
            "opcode": self.opcode,
            "name": self.name,
            "values": self.values,
            "shape_evidence": self.shape_evidence,
            "semantic_evidence": self.semantic_evidence,
        }
        if self.raw is not None:
            result["raw"] = self.raw
        return result


@dataclass
class RawSpan:
    """Uninterpreted bytes retained verbatim for lossless round trips."""

    offset: int
    raw: str

    def encode(self, registry: CommandRegistry) -> bytes:
        del registry
        self.offset = _offset(self.offset)
        return _hex_bytes(self.raw, "Raw record")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "raw",
            "offset": self.offset,
            "raw": self.raw,
        }


Record: TypeAlias = KnownCommand | RawSpan


def decode_frame(
    data: bytes,
    offset: int,
    registry: CommandRegistry,
) -> tuple[Record, str | None]:
    """Apply a semantic shape to exactly one lexical command frame."""
    if not data or not is_command_start(data[0]):
        return (
            RawSpan(offset, data.hex()),
            f"Unframed bytes at {offset}:{offset + len(data)}",
        )
    candidates = registry.candidates(data)
    if not candidates:
        return (
            RawSpan(offset, data.hex()),
            f"Unknown command frame at {offset}:{offset + len(data)}",
        )
    matches = []
    errors = []
    for spec in candidates:
        try:
            values, end = spec.decode(data, 0)
        except (FieldError, ValueError) as error:
            errors.append(f"{spec.name}: {error}")
            continue
        if end == len(data):
            matches.append((spec, values))
        else:
            errors.append(
                f"{spec.name}: consumed {end} of {len(data)} bytes"
            )
    if not matches:
        return (
            RawSpan(offset, data.hex()),
            f"No semantic shape fits frame at {offset}: " + "; ".join(errors),
        )
    if len(matches) > 1:
        names = ", ".join(spec.name for spec, _ in matches)
        return (
            RawSpan(offset, data.hex()),
            f"Ambiguous command frame at {offset}: {names}",
        )
    spec, values = matches[0]
    return (
        KnownCommand(
            offset=offset,
            opcode=spec.opcode.hex(),
            name=spec.name,
            values=values,
            raw=data.hex(),
            shape_evidence=spec.shape_evidence,
            semantic_evidence=spec.semantic_evidence,
        ),
        None,
    )


@dataclass
class Program:
    """One decoded Ruida file, including any wrapper and unknown bytes."""

    magic: int = 0x88
    context: str = "job"
    container: str = "rd"
    header: str = ""
    records: list[Record] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    source_checksum_basis: int | None = None
    _registry: CommandRegistry | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def _validate_metadata(self) -> bytes:
        self.magic = json_integer(
            self.magic,
            "Magic value",
            minimum=0,
            maximum=0xFF,
        )
        if self.context not in ("job", "request", "reply"):
            raise ValueError(f"Unknown protocol context: {self.context}")
        if self.container not in CONTAINERS:
            raise ValueError(f"Unknown container: {self.container}")
        header = _hex_bytes(self.header, "Program header")
        if header and (
            len(header) != 10 or not header.startswith(b"RDWORKV")
        ):
            raise ValueError(
                "Program header must be a ten-byte RDWORKV wrapper"
            )
        if self.container != "rd" and header:
            raise ValueError(
                f"The {self.container} container cannot have an RDWORKV "
                "header"
            )
        if not isinstance(self.records, list):
            raise ValueError("Program records must be a list")
        if not all(
            isinstance(record, (KnownCommand, RawSpan))
            for record in self.records
        ):
            raise ValueError("Program contains an unknown record type")
        if not isinstance(self.issues, list) or not all(
            isinstance(issue, str) for issue in self.issues
        ):
            raise ValueError("Program issues must be a list of strings")
        if self.source_checksum_basis is not None:
            self.source_checksum_basis = json_integer(
                self.source_checksum_basis,
                "Source checksum basis",
                minimum=0,
            )
        return header

    def validate(self, registry: CommandRegistry | None = None) -> None:
        """Validate metadata and every structured record."""
        self._validate_metadata()
        if registry is None:
            registry = self._registry or get_registry(self.context)
        for record in self.records:
            record.encode(registry)

    def encode(
        self,
        registry: CommandRegistry | None = None,
        checksum_policy: str = "preserve",
    ) -> bytes:
        header = self._validate_metadata()
        if registry is None:
            registry = self._registry or get_registry(self.context)
        parts = [record.encode(registry) for record in self.records]
        if checksum_policy not in ("preserve", "recompute"):
            raise ValueError(
                "Checksum policy must be 'preserve' or 'recompute'"
            )
        if checksum_policy == "recompute":
            checksum_indexes = [
                index
                for index, record in enumerate(self.records)
                if isinstance(record, KnownCommand)
                and record.name == "file_checksum"
            ]
            checksum_set = set(checksum_indexes)
            checksum_basis = sum(
                byte
                for index, part in enumerate(parts)
                if index not in checksum_set
                for byte in part
            )
            if len(checksum_indexes) > 1:
                raise ValueError(
                    "Cannot update a stream with multiple file checksums"
                )
            if len(checksum_indexes) == 1:
                checksum_index = checksum_indexes[0]
                spec = registry.name("file_checksum")
                if spec is None:
                    raise ValueError(
                        "The registry has no file checksum command"
                    )
                parts[checksum_index] = spec.encode(
                    {"value": checksum_basis}
                )
        stream = b"".join(parts)
        if self.container == "logical":
            return stream
        if self.container == "udp":
            return encode_datagram(stream, self.context, self.magic)
        scrambled = swizzle(stream, self.magic)
        return header + scrambled

    def to_dict(
        self,
        registry: CommandRegistry | None = None,
    ) -> dict[str, Any]:
        self.validate(registry)
        return {
            "schema": SCHEMA,
            "magic": self.magic,
            "context": self.context,
            "container": self.container,
            "header": self.header,
            "records": [record.to_dict() for record in self.records],
            "issues": self.issues,
            "source_checksum_basis": self.source_checksum_basis,
        }

    def to_json(
        self,
        indent: int | None = 2,
        *,
        registry: CommandRegistry | None = None,
    ) -> str:
        return json.dumps(
            self.to_dict(registry),
            allow_nan=False,
            indent=indent,
        ) + "\n"

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        registry: CommandRegistry | None = None,
    ) -> Program:
        if not isinstance(data, dict):
            raise ValueError("The program document must be a JSON object")
        if data.get("schema") != SCHEMA:
            raise ValueError(f"Unsupported schema: {data.get('schema')!r}")
        required = {
            "schema",
            "magic",
            "context",
            "container",
            "header",
            "records",
            "issues",
            "source_checksum_basis",
        }
        actual = set(data)
        if actual != required:
            raise ValueError(
                "Program document fields do not match: "
                f"missing={sorted(required - actual)}, "
                f"extra={sorted(actual - required)}"
            )
        items = data["records"]
        if not isinstance(items, list):
            raise ValueError("Program records must be a list")
        records: list[Record] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each program record must be an object")
            if item.get("kind") == "command":
                expected = {
                    "kind",
                    "offset",
                    "opcode",
                    "name",
                    "values",
                    "shape_evidence",
                    "semantic_evidence",
                }
                if "raw" in item:
                    expected.add("raw")
                if set(item) != expected:
                    raise ValueError(
                        f"Command record fields do not match: {item!r}"
                    )
                records.append(
                    KnownCommand(
                        offset=item["offset"],
                        opcode=item["opcode"],
                        name=item["name"],
                        values=item["values"],
                        raw=item.get("raw"),
                        shape_evidence=item["shape_evidence"],
                        semantic_evidence=item["semantic_evidence"],
                    )
                )
            elif item.get("kind") == "raw":
                if set(item) != {"kind", "offset", "raw"}:
                    raise ValueError(
                        f"Raw record fields do not match: {item!r}"
                    )
                records.append(
                    RawSpan(
                        offset=item["offset"],
                        raw=item["raw"],
                    )
                )
            else:
                raise ValueError(f"Unknown record kind: {item.get('kind')!r}")
        program = cls(
            magic=data["magic"],
            context=data["context"],
            container=data["container"],
            header=data["header"],
            records=records,
            issues=data["issues"],
            source_checksum_basis=data["source_checksum_basis"],
            _registry=registry,
        )
        program.validate(registry)
        return program

    @classmethod
    def from_json(
        cls,
        value: str,
        registry: CommandRegistry | None = None,
    ) -> Program:
        data = load_json(value)
        if not isinstance(data, dict):
            raise ValueError("The program document must be a JSON object")
        return cls.from_dict(data, registry)


def split_wrapper(raw_data: bytes) -> tuple[bytes, bytes]:
    if raw_data.startswith(b"RDWORKV") and len(raw_data) >= 10:
        return raw_data[:10], raw_data[10:]
    return b"", raw_data


def decode(
    raw_data: bytes,
    magic: int = 0x88,
    registry: CommandRegistry | None = None,
    context: str = "job",
    container: str = "rd",
) -> Program:
    """Decode a Ruida file without discarding any input byte."""
    if (
        not isinstance(magic, int)
        or isinstance(magic, bool)
        or not 0 <= magic <= 0xFF
    ):
        raise ValueError("Magic value must fit in one byte")
    if registry is None:
        registry = get_registry(context)
    if container == "rd":
        header, body = split_wrapper(raw_data)
        data = unswizzle(body, magic)
    elif container == "udp":
        header = b""
        data = decode_datagram(raw_data, context, magic)
    elif container == "logical":
        header = b""
        data = raw_data
    else:
        raise ValueError(f"Unknown container: {container}")
    records: list[Record] = []
    issues: list[str] = []
    for offset, frame in logical_frames(data):
        record, issue = decode_frame(frame, offset, registry)
        records.append(record)
        if issue is not None:
            issues.append(issue)
    checksum_basis = sum(
        byte
        for record in records
        if not (
            isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        for byte in record.encode(registry)
    )
    return Program(
        magic=magic,
        context=context,
        container=container,
        header=header.hex(),
        records=records,
        issues=issues,
        source_checksum_basis=checksum_basis,
        _registry=registry,
    )


def decode_path(
    path: Path,
    magic: int = 0x88,
    context: str = "job",
    container: str = "rd",
) -> Program:
    return decode(
        path.read_bytes(),
        magic,
        context=context,
        container=container,
    )
