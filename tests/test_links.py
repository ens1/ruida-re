"""Tests for Ruida wire strategies over raw transports."""

from __future__ import annotations

import unittest

from ruida_re.codec import swizzle
from ruida_re.links import SerialLink, UdpLink, link_for_transport
from ruida_re.transport import unframe


class FakeTransport:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.is_open = True
        self.sent: list[bytes] = []
        self.received: list[bytes] = []

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def receive(self, timeout: float) -> bytes | None:
        del timeout
        return self.received.pop(0) if self.received else None

    def drain(self, limit: int = 256) -> tuple[bytes, ...]:
        del limit
        result = tuple(self.received)
        self.received.clear()
        return result


class LinkStrategyTest(unittest.TestCase):
    def test_udp_packetizes_with_checksum_and_arbitrary_boundaries(
        self,
    ) -> None:
        transport = FakeTransport("udp")
        link = UdpLink(transport)
        logical = bytes.fromhex("da000005ce")
        packets = link.packetize(logical, chunk_size=3)
        self.assertEqual(
            [packet.logical for packet in packets],
            [logical[:3], logical[3:]],
        )
        self.assertEqual(
            [unframe(packet.raw) for packet in packets],
            [swizzle(logical[:3]), swizzle(logical[3:])],
        )

    def test_serial_packetizes_as_a_scrambled_stream(self) -> None:
        transport = FakeTransport("serial")
        link = SerialLink(transport)
        logical = bytes.fromhex("da000005")
        packets = link.packetize(logical, chunk_size=2)
        self.assertEqual(
            [packet.raw for packet in packets],
            [swizzle(logical[:2]), swizzle(logical[2:])],
        )
        self.assertFalse(link.acknowledgement_required)

    def test_inbound_reads_are_unscrambled_without_udp_unframing(
        self,
    ) -> None:
        transport = FakeTransport("udp")
        transport.received.append(swizzle(bytes.fromhex("cc")))
        unit = UdpLink(transport).receive(0.1)
        self.assertIsNotNone(unit)
        self.assertEqual(unit.logical, bytes.fromhex("cc"))

    def test_factory_selects_only_at_the_link_boundary(self) -> None:
        udp = FakeTransport("udp")
        serial = FakeTransport("serial")
        self.assertIsInstance(link_for_transport(udp), UdpLink)
        self.assertIsInstance(link_for_transport(serial), SerialLink)
        unknown = FakeTransport("custom")
        with self.assertRaises(ValueError):
            link_for_transport(unknown)

    def test_link_rejects_boolean_and_noninteger_magic(self) -> None:
        transport = FakeTransport("udp")
        for magic in (True, float("nan"), 256, 10**400):
            with self.subTest(magic=magic):
                with self.assertRaises(ValueError):
                    UdpLink(transport, magic=magic)


if __name__ == "__main__":
    unittest.main()
