"""Tests for command decoding across arbitrary input boundaries."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ruida_re.codec import swizzle, unswizzle
from ruida_re.program import decode
from ruida_re.registry import DEFAULT_REGISTRY, REGISTRIES
from ruida_re.stream import StreamDecoder

from sample_values import sample_value


ROOT = Path(__file__).resolve().parents[1]
RD_PATH = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"


class StreamDecoderTest(unittest.TestCase):
    def test_single_byte_chunks_match_whole_file_decode(self) -> None:
        raw_data = RD_PATH.read_bytes()
        logical = unswizzle(raw_data)
        decoder = StreamDecoder()
        records = []
        for byte in logical:
            records.extend(decoder.feed(bytes((byte,))))
        records.extend(decoder.finish())
        expected = decode(raw_data)
        self.assertEqual(
            [record.to_dict() for record in records],
            [record.to_dict() for record in expected.records],
        )
        rebuilt = b"".join(
            record.encode(DEFAULT_REGISTRY) for record in records
        )
        self.assertEqual(rebuilt, logical)

    def test_opcode_prefix_waits_for_the_next_chunk(self) -> None:
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(b"\xc6"), [])
        records = decoder.feed(b"\x31\x00\x0c\x66", final=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "layer_laser_1_min_power")

    def test_overlapping_opcode_is_chunk_independent(self) -> None:
        logical = bytes.fromhex("a75300 d7")
        expected = decode(swizzle(logical)).records
        decoder = StreamDecoder()
        actual = decoder.feed(logical[:2])
        actual.extend(decoder.feed(logical[2:]))
        actual.extend(decoder.finish())
        self.assertEqual(actual, expected)

    def test_every_command_is_independent_of_split_position(self) -> None:
        for context, registry in REGISTRIES.items():
            for spec in registry:
                values = {
                    field.name: sample_value(field)
                    for field in spec.fields
                }
                logical = spec.encode(values) + b"\xd7"
                expected = decode(
                    logical,
                    registry=registry,
                    context=context,
                    container="logical",
                ).records
                for split in range(len(logical) + 1):
                    with self.subTest(
                        context=context,
                        command=spec.name,
                        split=split,
                    ):
                        decoder = StreamDecoder(registry)
                        actual = decoder.feed(logical[:split])
                        actual.extend(decoder.feed(logical[split:]))
                        actual.extend(decoder.finish())
                        self.assertEqual(actual, expected)

    def test_dense_large_feed_does_not_rescan_suffixes(self) -> None:
        size = 50_000
        decoder = StreamDecoder()
        with patch(
            "ruida_re.stream.next_frame_boundary",
            create=True,
            side_effect=AssertionError("suffix rescan"),
        ):
            records = decoder.feed(bytes([0xD7]) * size, final=True)
        self.assertEqual(len(records), size)
        self.assertEqual(decoder.offset, size)


if __name__ == "__main__":
    unittest.main()
