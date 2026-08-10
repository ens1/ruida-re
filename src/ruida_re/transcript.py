"""Versioned, lossless transcripts of Ruida UDP datagrams."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .jsonio import integer as json_integer
from .jsonio import loads as load_json
from .jsonio import number as json_number
from .program import Program, decode
from .transport import decode_datagram


SCHEMA = "ruida-re.transcript.v1"
DIRECTION_CONTEXTS = {
    "outbound": frozenset(("job", "request")),
    "inbound": frozenset(("reply",)),
}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _issues(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError("Datagram issues must be a list of strings")
    return list(value)


def _exact_fields(
    data: dict[str, Any],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    actual = set(data)
    missing = required - actual
    extra = actual - required - optional
    if missing or extra:
        raise ValueError(
            f"{label} fields do not match: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )


def _hex_bytes(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a hexadecimal string")
    if (
        len(value) % 2
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(
            f"{label} must use canonical lowercase hexadecimal"
        )
    return bytes.fromhex(value)


def _validate_direction(direction: str, context: str) -> None:
    contexts = DIRECTION_CONTEXTS.get(direction)
    if contexts is None:
        raise ValueError(f"Unknown datagram direction: {direction}")
    if context not in contexts:
        expected = ", ".join(sorted(contexts))
        raise ValueError(
            f"Context {context} is invalid for {direction}; "
            f"expected one of: {expected}"
        )


@dataclass(frozen=True)
class Endpoint:
    """One optional UDP endpoint attached to a captured datagram."""

    address: str
    port: int

    def __post_init__(self) -> None:
        if not isinstance(self.address, str) or not self.address:
            raise ValueError("Endpoint address must be a nonempty string")
        port = json_integer(
            self.port,
            "Endpoint port",
            minimum=0,
            maximum=0xFFFF,
        )
        object.__setattr__(self, "port", port)

    def to_dict(self) -> dict[str, Any]:
        return {"address": self.address, "port": self.port}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Endpoint:
        data = _mapping(data, "endpoint")
        _exact_fields(
            data,
            {"address", "port"},
            set(),
            "Endpoint",
        )
        return cls(address=data["address"], port=data["port"])


@dataclass
class Datagram:
    """One captured UDP datagram and its direction-aware interpretation."""

    direction: str
    context: str
    raw: str
    program: Program | None
    issues: list[str] = field(default_factory=list)
    timestamp: int | float | None = None
    source: Endpoint | None = None
    destination: Endpoint | None = None

    def __post_init__(self) -> None:
        self.issues = _issues(self.issues)
        self._validate_metadata()
        self._validate_program()

    def _validate_metadata(self) -> None:
        _validate_direction(self.direction, self.context)
        self.raw_bytes()
        _issues(self.issues)
        if self.timestamp is not None:
            self.timestamp = json_number(
                self.timestamp,
                "Datagram timestamp",
            )
        if self.source is not None and not isinstance(self.source, Endpoint):
            raise ValueError("Datagram source must be an Endpoint")
        if (
            self.destination is not None
            and not isinstance(self.destination, Endpoint)
        ):
            raise ValueError("Datagram destination must be an Endpoint")

    def _validate_program(self) -> None:
        if self.program is None:
            if not self.issues:
                raise ValueError(
                    "A datagram without a decoded program needs an issue"
                )
            return
        if not isinstance(self.program, Program):
            raise ValueError("Datagram program must be a Program")
        if self.program.container != "udp":
            raise ValueError("Datagram program must use the UDP container")
        if self.program.context != self.context:
            raise ValueError(
                "Datagram context does not match its decoded program"
            )
        try:
            encoded = self.program.encode()
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"Datagram program cannot reproduce its raw bytes: {error}"
            ) from error
        if encoded != self.raw_bytes():
            raise ValueError(
                "Datagram program does not reproduce its raw bytes"
            )

    def raw_bytes(self) -> bytes:
        return _hex_bytes(self.raw, "Datagram raw value")

    def to_dict(self) -> dict[str, Any]:
        self._validate_metadata()
        self._validate_program()
        result: dict[str, Any] = {
            "direction": self.direction,
            "context": self.context,
            "raw": self.raw,
            "program": (
                None if self.program is None else self.program.to_dict()
            ),
            "issues": list(self.issues),
        }
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp
        if self.source is not None:
            result["source"] = self.source.to_dict()
        if self.destination is not None:
            result["destination"] = self.destination.to_dict()
        return result

    @classmethod
    def from_bytes(
        cls,
        raw_data: bytes,
        direction: str,
        context: str,
        *,
        timestamp: int | float | None = None,
        source: Endpoint | None = None,
        destination: Endpoint | None = None,
        magic: int = 0x88,
    ) -> Datagram:
        _validate_direction(direction, context)
        try:
            program = decode(
                raw_data,
                magic=magic,
                context=context,
                container="udp",
            )
        except ValueError as error:
            return cls(
                direction=direction,
                context=context,
                raw=raw_data.hex(),
                program=None,
                issues=[f"{type(error).__name__}: {error}"],
                timestamp=timestamp,
                source=source,
                destination=destination,
            )
        return cls(
            direction=direction,
            context=context,
            raw=raw_data.hex(),
            program=program,
            issues=list(program.issues),
            timestamp=timestamp,
            source=source,
            destination=destination,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Datagram:
        data = _mapping(data, "datagram")
        _exact_fields(
            data,
            {"direction", "context", "raw", "program", "issues"},
            {"timestamp", "source", "destination"},
            "Datagram",
        )
        program_data = data["program"]
        program = (
            None
            if program_data is None
            else Program.from_dict(_mapping(program_data, "program"))
        )
        source_data = data.get("source")
        destination_data = data.get("destination")
        return cls(
            direction=data["direction"],
            context=data["context"],
            raw=data["raw"],
            program=program,
            issues=_issues(data["issues"]),
            timestamp=data.get("timestamp"),
            source=(
                None
                if source_data is None
                else Endpoint.from_dict(_mapping(source_data, "source"))
            ),
            destination=(
                None
                if destination_data is None
                else Endpoint.from_dict(
                    _mapping(destination_data, "destination")
                )
            ),
        )


@dataclass
class Transcript:
    """An ordered, boundary-preserving collection of UDP datagrams."""

    datagrams: list[Datagram] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.datagrams = list(self.datagrams)
        if not all(
            isinstance(datagram, Datagram) for datagram in self.datagrams
        ):
            raise ValueError("Transcript entries must be datagrams")

    def capture(
        self,
        raw_data: bytes,
        direction: str,
        context: str,
        *,
        timestamp: int | float | None = None,
        source: Endpoint | None = None,
        destination: Endpoint | None = None,
        magic: int = 0x88,
    ) -> Datagram:
        datagram = capture_datagram(
            raw_data,
            direction,
            context,
            timestamp=timestamp,
            source=source,
            destination=destination,
            magic=magic,
        )
        self.datagrams.append(datagram)
        return datagram

    def raw_datagrams(self) -> tuple[bytes, ...]:
        return tuple(datagram.raw_bytes() for datagram in self.datagrams)

    def decode_flow(
        self,
        direction: str,
        context: str,
        *,
        start: int = 0,
        stop: int | None = None,
        magic: int = 0x88,
    ) -> Program:
        """Reassemble one directional flow across packet boundaries."""
        _validate_direction(direction, context)
        selected = self.datagrams[slice(start, stop)]
        logical = bytearray()
        matched = False
        for datagram in selected:
            if (
                datagram.direction != direction
                or datagram.context != context
            ):
                continue
            matched = True
            logical.extend(
                decode_datagram(datagram.raw_bytes(), context, magic)
            )
        if not matched:
            raise ValueError(
                f"Transcript range has no {direction} {context} datagrams"
            )
        return decode(
            bytes(logical),
            magic=magic,
            context=context,
            container="logical",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "datagrams": [datagram.to_dict() for datagram in self.datagrams],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            indent=indent,
        ) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        data = _mapping(data, "transcript")
        _exact_fields(
            data,
            {"schema", "datagrams"},
            set(),
            "Transcript",
        )
        if data.get("schema") != SCHEMA:
            raise ValueError(f"Unsupported schema: {data.get('schema')!r}")
        items = data.get("datagrams")
        if not isinstance(items, list):
            raise ValueError("Transcript datagrams must be a list")
        return cls(
            datagrams=[
                Datagram.from_dict(_mapping(item, "datagram"))
                for item in items
            ]
        )

    @classmethod
    def from_json(cls, value: str) -> Transcript:
        data = load_json(value)
        return cls.from_dict(_mapping(data, "transcript"))


def capture_datagram(
    raw_data: bytes,
    direction: str,
    context: str,
    *,
    timestamp: int | float | None = None,
    source: Endpoint | None = None,
    destination: Endpoint | None = None,
    magic: int = 0x88,
) -> Datagram:
    """Decode one captured datagram without losing malformed input."""
    return Datagram.from_bytes(
        raw_data,
        direction,
        context,
        timestamp=timestamp,
        source=source,
        destination=destination,
        magic=magic,
    )
