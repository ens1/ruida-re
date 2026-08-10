"""Ruida wire strategies layered over raw byte transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .codec import swizzle, unswizzle
from .transport import frame, payload_chunks
from .transports import ControllerTransport


@dataclass(frozen=True)
class OutboundPacket:
    """One transport write and the logical bytes represented by it."""

    raw: bytes
    logical: bytes


@dataclass(frozen=True)
class InboundUnit:
    """One successful transport read after Ruida wire decoding."""

    raw: bytes
    logical: bytes


@runtime_checkable
class RuidaLink(Protocol):
    """Ruida-specific framing strategy over a raw transport."""

    transport: ControllerTransport
    name: str
    magic: int
    acknowledgement_required: bool
    control_datagrams: bool
    receive_boundaries: Literal["datagram", "stream"]

    @property
    def is_open(self) -> bool:
        ...

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    def packetize(
        self,
        logical: bytes,
        chunk_size: int,
    ) -> tuple[OutboundPacket, ...]:
        ...

    def send(self, packet: OutboundPacket) -> None:
        ...

    def receive(self, timeout: float) -> InboundUnit | None:
        ...


class _BaseLink:
    """Shared bytewise scrambling for concrete Ruida links."""

    name = "base"
    acknowledgement_required = False
    control_datagrams = False
    receive_boundaries: Literal["datagram", "stream"] = "stream"

    def __init__(
        self,
        transport: ControllerTransport,
        *,
        magic: int = 0x88,
    ) -> None:
        if (
            type(magic) is not int
            or not 0 <= magic <= 0xFF
        ):
            raise ValueError("Magic value must fit in one byte")
        self.transport = transport
        self.magic = magic

    @property
    def is_open(self) -> bool:
        return self.transport.is_open

    def open(self) -> None:
        self.transport.open()

    def close(self) -> None:
        self.transport.close()

    def send(self, packet: OutboundPacket) -> None:
        self.transport.send(packet.raw)

    def receive(self, timeout: float) -> InboundUnit | None:
        raw = self.transport.receive(timeout)
        if raw is None:
            return None
        return InboundUnit(raw=raw, logical=unswizzle(raw, self.magic))


class UdpLink(_BaseLink):
    """Direct-controller UDP framing and acknowledgement strategy."""

    name = "udp"
    acknowledgement_required = True
    control_datagrams = True
    receive_boundaries: Literal["datagram", "stream"] = "datagram"

    def packetize(
        self,
        logical: bytes,
        chunk_size: int,
    ) -> tuple[OutboundPacket, ...]:
        return tuple(
            OutboundPacket(
                raw=frame(swizzle(chunk, self.magic)),
                logical=chunk,
            )
            for chunk in payload_chunks(logical, chunk_size)
        )


class SerialLink(_BaseLink):
    """Ruida USB-serial byte stream without UDP framing or ACKs."""

    name = "serial"

    def packetize(
        self,
        logical: bytes,
        chunk_size: int,
    ) -> tuple[OutboundPacket, ...]:
        return tuple(
            OutboundPacket(
                raw=swizzle(chunk, self.magic),
                logical=chunk,
            )
            for chunk in payload_chunks(logical, chunk_size)
        )


def link_for_transport(
    transport: ControllerTransport,
    *,
    magic: int = 0x88,
) -> RuidaLink:
    """Select a built-in link once at the raw-transport boundary."""
    if transport.kind == "udp":
        return UdpLink(transport, magic=magic)
    if transport.kind == "serial":
        return SerialLink(transport, magic=magic)
    raise ValueError(
        f"No built-in Ruida link for transport kind {transport.kind!r}"
    )
