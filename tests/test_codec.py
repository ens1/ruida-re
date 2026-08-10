"""Unit tests for the protocol's value and byte transforms."""

from __future__ import annotations

import unittest

from ruida_re.codec import (
    decode_mm,
    decode_power,
    decode_s14,
    decode_s32,
    decode_s35,
    decode_u14,
    decode_u35,
    encode_mm,
    encode_power,
    encode_s14,
    encode_s32,
    encode_s35,
    encode_u14,
    encode_u35,
    swizzle,
    unswizzle,
)


class ByteTransformTest(unittest.TestCase):
    def test_every_byte_round_trips(self) -> None:
        values = bytes(range(256))
        self.assertEqual(unswizzle(swizzle(values)), values)
        self.assertEqual(swizzle(unswizzle(values)), values)


class NumberCodecTest(unittest.TestCase):
    def test_u14_boundaries_round_trip(self) -> None:
        for value in (0, 1, 127, 128, 0x3FFF):
            self.assertEqual(decode_u14(encode_u14(value)), value)

    def test_u35_boundaries_round_trip(self) -> None:
        for value in (0, 1, 127, 128, 20_000, (1 << 35) - 1):
            self.assertEqual(decode_u35(encode_u35(value)), value)

    def test_signed_boundaries_round_trip(self) -> None:
        for value in (-(1 << 13), -1, 0, 1, (1 << 13) - 1):
            self.assertEqual(decode_s14(encode_s14(value)), value)
        for value in (-(1 << 34), -1, 0, 1, (1 << 34) - 1):
            self.assertEqual(decode_s35(encode_s35(value)), value)

    def test_signed_32_in_five_groups_round_trip(self) -> None:
        for value in (-(1 << 31), -1000, -1, 0, 1, (1 << 31) - 1):
            encoded = encode_s32(value)
            self.assertLess(encoded[0], 0x10)
            self.assertEqual(decode_s32(encoded), value)

    def test_negative_mm_uses_32_bit_sign(self) -> None:
        encoded = bytes.fromhex("0f7f7f7818")
        self.assertEqual(encode_mm(-1), encoded)
        self.assertEqual(decode_mm(encoded), -1)
        self.assertEqual(decode_s32(bytes.fromhex("7f7f7f7f7f")), -1)

    def test_unsigned_encoders_reject_out_of_range_values(self) -> None:
        for encoder, values in (
            (encode_u14, (-1, 1 << 14)),
            (encode_u35, (-1, 1 << 35)),
        ):
            for value in values:
                with self.assertRaises(ValueError):
                    encoder(value)

    def test_physical_values_round_trip(self) -> None:
        self.assertEqual(decode_mm(encode_mm(20.0)), 20.0)
        self.assertAlmostEqual(
            decode_power(encode_power(10.0)),
            10.0,
            places=2,
        )


if __name__ == "__main__":
    unittest.main()
