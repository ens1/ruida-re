"""Tests for the logical stream's seven-bit field invariant."""

from __future__ import annotations

import unittest

from ruida_re.fields import ByteField, BytesField, CStringField, FieldError


class SevenBitFieldTest(unittest.TestCase):
    def test_byte_decoder_rejects_a_command_start(self) -> None:
        with self.assertRaises(FieldError):
            ByteField("value").decode(b"\x80", 0)

    def test_opaque_bytes_are_still_seven_bit(self) -> None:
        field = BytesField("value", 1)
        with self.assertRaises(FieldError):
            field.decode(b"\x80", 0)
        with self.assertRaises(FieldError):
            field.encode("80")

    def test_cstring_cannot_encode_a_command_start(self) -> None:
        with self.assertRaises(FieldError):
            CStringField("value").encode("80")


if __name__ == "__main__":
    unittest.main()
