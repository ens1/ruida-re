"""Lossless Ruida file decoding, editing, and encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

from .codec import swizzle, unswizzle
from .fields import FieldError
from .registry import get_registry
from .specs import CommandRegistry
from .syntax import is_command_start, logical_frames
from .transport import decode_datagram, encode_datagram


SCHEMA = "ruida-re.program.v1"
CONTAINERS = ("rd", "udp", "logical")


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

    def encode(self, registry: CommandRegistry) -> bytes:
        opcode = bytes.fromhex(self.opcode)
        spec = registry.name(self.name)
        if spec is None:
            raise ValueError(f"Unknown structured command: {self.name}")
        if opcode != spec.opcode:
            raise ValueError(
                f"Command {self.name} uses opcode {spec.opcode.hex()}, "
                f"not {self.opcode}"
            )
        if self.raw is not None:
            raw = bytes.fromhex(self.raw)
            try:
                original, end = spec.decode(raw, 0)
            except (FieldError, ValueError):
                original = None
                end = -1
            if end == len(raw) and original == self.values:
                return raw
        return spec.encode(self.values)

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
        return bytes.fromhex(self.raw)

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

    def encode(
        self,
        registry: CommandRegistry | None = None,
        checksum_policy: str = "preserve",
    ) -> bytes:
        if not 0 <= self.magic <= 0xFF:
            raise ValueError("Magic value must fit in one byte")
        if registry is None:
            registry = get_registry(self.context)
        header = bytes.fromhex(self.header)
        if self.container not in CONTAINERS:
            raise ValueError(f"Unknown container: {self.container}")
        if self.container != "rd" and header:
            raise ValueError(
                f"The {self.container} container cannot have an RDWORKV header"
            )
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Program:
        if data.get("schema") != SCHEMA:
            raise ValueError(f"Unsupported schema: {data.get('schema')!r}")
        records: list[Record] = []
        for item in data.get("records", []):
            if item.get("kind") == "command":
                records.append(
                    KnownCommand(
                        offset=item.get("offset", 0),
                        opcode=item["opcode"],
                        name=item["name"],
                        values=item["values"],
                        raw=item.get("raw"),
                        shape_evidence=item.get(
                            "shape_evidence",
                            "reported",
                        ),
                        semantic_evidence=item.get(
                            "semantic_evidence",
                            "reported",
                        ),
                    )
                )
            elif item.get("kind") == "raw":
                records.append(
                    RawSpan(
                        offset=item.get("offset", 0),
                        raw=item["raw"],
                    )
                )
            else:
                raise ValueError(f"Unknown record kind: {item.get('kind')!r}")
        return cls(
            magic=data.get("magic", 0x88),
            context=data.get("context", "job"),
            container=data.get("container", "rd"),
            header=data.get("header", ""),
            records=records,
            issues=list(data.get("issues", [])),
            source_checksum_basis=data.get("source_checksum_basis"),
        )

    @classmethod
    def from_json(cls, value: str) -> Program:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("The program document must be a JSON object")
        return cls.from_dict(data)


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
    if not 0 <= magic <= 0xFF:
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
