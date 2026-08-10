"""Tests for the external-fixture verification report."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from ruida_re.verify import verify


ROOT = Path(__file__).resolve().parents[1]
RD_PATH = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"


class VerifyTest(unittest.TestCase):
    def test_baseline_report(self) -> None:
        digest = hashlib.sha256(RD_PATH.read_bytes()).hexdigest()
        report = verify(RD_PATH, expected_sha256=digest)
        self.assertTrue(report["sha256_matches"])
        self.assertTrue(report["direct_exact"])
        self.assertTrue(report["json_exact"])
        self.assertEqual(report["known_records"], 70)
        self.assertEqual(report["opaque_records"], 0)

    def test_wrong_digest_is_reported(self) -> None:
        report = verify(RD_PATH, expected_sha256="0" * 64)
        self.assertFalse(report["sha256_matches"])


if __name__ == "__main__":
    unittest.main()
