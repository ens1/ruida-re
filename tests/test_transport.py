"""Tests for controller packet framing."""

from __future__ import annotations

import unittest

from ruida_re.transport import (
    ChecksumError,
    decode_datagram,
    decode_packet,
    encode_datagram,
    encode_packet,
    frame,
    frame_chunks,
    unframe,
)


class TransportFramingTest(unittest.TestCase):
    def test_packet_round_trip(self) -> None:
        payload = bytes(range(256))
        self.assertEqual(unframe(frame(payload)), payload)

    def test_logical_packet_pipeline_uses_scrambled_checksum(self) -> None:
        logical = bytes.fromhex("da000001")
        packet = encode_packet(logical)
        self.assertEqual(packet, bytes.fromhex("01efd4898909"))
        self.assertEqual(decode_packet(packet), logical)

    def test_reply_datagram_has_no_checksum_prefix(self) -> None:
        datagram = encode_datagram(b"\xcc", context="reply")
        self.assertEqual(datagram, bytes.fromhex("c6"))
        self.assertEqual(
            decode_datagram(datagram, context="reply"),
            b"\xcc",
        )

    def test_request_datagram_has_a_checksum_prefix(self) -> None:
        logical = bytes.fromhex("da000001")
        datagram = encode_datagram(logical, context="request")
        self.assertEqual(
            decode_datagram(datagram, context="request"),
            logical,
        )

    def test_bad_checksum_is_rejected(self) -> None:
        packet = bytearray(frame(b"payload"))
        packet[-1] ^= 1
        with self.assertRaises(ChecksumError):
            unframe(bytes(packet))

    def test_chunking_respects_payload_mtu(self) -> None:
        data = bytes(range(250)) * 13
        packets = list(frame_chunks(data, mtu=1000))
        sizes = [len(packet) for packet in packets]
        self.assertEqual(sizes, [1002, 1002, 1002, 252])
        self.assertEqual(b"".join(unframe(packet) for packet in packets), data)


if __name__ == "__main__":
    unittest.main()
