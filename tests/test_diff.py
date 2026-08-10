"""Tests for comparisons of scrambled Ruida streams."""

from __future__ import annotations

import unittest

from ruida_re.codec import swizzle
from ruida_re.diff import Change, compare


class DiffTest(unittest.TestCase):
    def test_equal_streams_have_no_changes(self) -> None:
        raw_data = swizzle(b"\xc9\x04\x00")
        self.assertEqual(compare(raw_data, raw_data), [])

    def test_reports_offsets_and_unscrambled_values(self) -> None:
        before = swizzle(b"\xc6\x31\x00\x0c\x66\xd7")
        after = swizzle(b"\xc6\x31\x00\x0d\x66\xd7")
        self.assertEqual(
            compare(before, after),
            [Change("replace", 3, 4, 3, 4, b"\x0c", b"\x0d")],
        )

    def test_wrapper_does_not_change_logical_offsets(self) -> None:
        header = b"RDWORKV123"
        before = header + swizzle(b"\xd7")
        after = header + swizzle(b"\xcc")
        changes = compare(before, after)
        self.assertEqual(
            changes,
            [Change("replace", 0, 1, 0, 1, b"\xd7", b"\xcc")],
        )


if __name__ == "__main__":
    unittest.main()
