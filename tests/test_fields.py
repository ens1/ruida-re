"""Tests for the logical stream's seven-bit field invariant."""

from __future__ import annotations

import unittest

from ruida_re.fields import (
    AbsoluteMmField,
    ByteField,
    BytesField,
    CStringField,
    FieldError,
    PowerField,
    U14Field,
)


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

    def test_boolean_is_not_an_integer_or_number(self) -> None:
        for field in (
            ByteField("value"),
            U14Field("value"),
            AbsoluteMmField("value"),
            PowerField("value"),
        ):
            with self.subTest(field=type(field).__name__):
                with self.assertRaises(FieldError):
                    field.encode(True)

    def test_numeric_fields_reject_nonfinite_values(self) -> None:
        field = AbsoluteMmField("value")
        for value in (
            float("nan"),
            float("inf"),
            -float("inf"),
            10**10000,
        ):
            with self.subTest(value=value):
                with self.assertRaises(FieldError):
                    field.encode(value)

    def test_hex_fields_require_canonical_lowercase_text(self) -> None:
        field = BytesField("value", 2)
        self.assertEqual(field.encode("0a7f"), bytes.fromhex("0a7f"))
        for value in ("0A7F", "0a 7f", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(FieldError):
                    field.encode(value)


if __name__ == "__main__":
    unittest.main()
