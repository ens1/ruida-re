"""Tests for grammar-level logical command framing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ruida_re.syntax import logical_frames, next_frame_boundary


class LogicalFramingTest(unittest.TestCase):
    def test_frames_use_only_high_bit_boundaries(self) -> None:
        data = bytes.fromhex("0102 c631000c66 d7")
        frames = list(logical_frames(data))
        self.assertEqual(
            frames,
            [
                (0, bytes.fromhex("0102")),
                (2, bytes.fromhex("c631000c66")),
                (7, bytes.fromhex("d7")),
            ],
        )

    def test_partial_frame_has_no_boundary(self) -> None:
        self.assertIsNone(next_frame_boundary(bytes.fromhex("c63100")))
        self.assertEqual(
            next_frame_boundary(bytes.fromhex("c63100 d7")),
            3,
        )

    def test_dense_large_stream_uses_one_lexical_pass(self) -> None:
        size = 250_000
        data = bytes([0x80]) * size
        with patch(
            "ruida_re.syntax.next_frame_boundary",
            side_effect=AssertionError("suffix rescan"),
        ):
            count = 0
            for offset, frame in logical_frames(data):
                self.assertEqual(offset, count)
                self.assertEqual(frame, b"\x80")
                count += 1
        self.assertEqual(count, size)


if __name__ == "__main__":
    unittest.main()
