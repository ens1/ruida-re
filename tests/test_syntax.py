"""Tests for grammar-level logical command framing."""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
