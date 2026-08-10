"""Tests for no-clobber and same-path CLI output safety."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ruida_re.cli_io import atomic_write_bytes, require_distinct_paths


class CliOutputSafetyTest(unittest.TestCase):
    def test_same_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.rd"
            path.write_bytes(b"source")
            with self.assertRaises(ValueError):
                require_distinct_paths(path, path)

    def test_output_is_no_clobber_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "output.rd"
            atomic_write_bytes(path, b"first")
            with self.assertRaises(FileExistsError):
                atomic_write_bytes(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")

    def test_force_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "output.rd"
            atomic_write_bytes(path, b"first")
            atomic_write_bytes(path, b"second", force=True)
            self.assertEqual(path.read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
