"""Packet framing primitives for Ruida controller transports."""

from __future__ import annotations

from collections.abc import Iterator

from .codec import swizzle, unswizzle


DEFAULT_MTU = 1470
DATAGRAM_CONTEXTS = ("job", "request", "reply")


class ChecksumError(ValueError):
    """Raised when a framed packet has an invalid checksum."""


def checksum(payload: bytes) -> int:
    """Return the Ruida 16-bit additive packet checksum."""
    return sum(payload) & 0xFFFF


def frame(payload: bytes) -> bytes:
    """Prefix a payload with its big-endian packet checksum."""
    value = checksum(payload)
    return value.to_bytes(2, "big") + payload


def unframe(packet: bytes) -> bytes:
    """Validate and remove a packet checksum prefix."""
    if len(packet) < 2:
        raise ChecksumError("A framed packet needs at least two bytes")
    expected = int.from_bytes(packet[:2], "big")
    payload = packet[2:]
    actual = checksum(payload)
    if actual != expected:
        raise ChecksumError(
            f"Packet checksum is 0x{expected:04x}, expected 0x{actual:04x}"
        )
    return payload


def payload_chunks(
    data: bytes,
    limit: int = DEFAULT_MTU,
) -> Iterator[bytes]:
    """Split bytes at transport boundaries without inspecting commands."""
    if limit <= 0:
        raise ValueError("Chunk limit must be positive")
    for offset in range(0, len(data), limit):
        yield data[offset : offset + limit]


def frame_chunks(data: bytes, mtu: int = DEFAULT_MTU) -> Iterator[bytes]:
    """Frame outbound chunks of an already-scrambled payload."""
    for chunk in payload_chunks(data, mtu):
        yield frame(chunk)


def encode_packet(logical_payload: bytes, magic: int = 0x88) -> bytes:
    """Scramble logical bytes, then create one outbound UDP packet."""
    return frame(swizzle(logical_payload, magic))


def decode_packet(packet: bytes, magic: int = 0x88) -> bytes:
    """Validate one outbound UDP packet, then return its logical bytes."""
    return unswizzle(unframe(packet), magic)


def encode_datagram(
    logical_payload: bytes,
    context: str,
    magic: int = 0x88,
) -> bytes:
    """Encode one direction-aware UDP datagram."""
    if context not in DATAGRAM_CONTEXTS:
        raise ValueError(f"Unknown datagram context: {context}")
    scrambled = swizzle(logical_payload, magic)
    if context == "reply":
        return scrambled
    return frame(scrambled)


def decode_datagram(
    datagram: bytes,
    context: str,
    magic: int = 0x88,
) -> bytes:
    """Decode one direction-aware UDP datagram."""
    if context not in DATAGRAM_CONTEXTS:
        raise ValueError(f"Unknown datagram context: {context}")
    scrambled = datagram if context == "reply" else unframe(datagram)
    return unswizzle(scrambled, magic)
