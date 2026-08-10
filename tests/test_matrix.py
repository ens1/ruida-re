"""Conformance tests for the controlled LightBurn fixture matrix."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from ruida_re.codec import decode_u14
from ruida_re.program import KnownCommand, decode


ROOT = Path(__file__).resolve().parents[1]
MATRIX_DIR = ROOT / "fixtures/lightburn-2.1.03/matrix"
MANIFEST_PATH = MATRIX_DIR / "matrix.json"


def command(program, name: str) -> KnownCommand:
    return next(
        record
        for record in program.records
        if isinstance(record, KnownCommand) and record.name == name
    )


class MatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.cases = {item["identifier"]: item for item in manifest["cases"]}
        cls.programs = {
            identifier: decode(
                (MATRIX_DIR / item["expected_rd"]).read_bytes()
            )
            for identifier, item in cls.cases.items()
        }

    def test_manifest_hashes(self) -> None:
        for item in self.cases.values():
            for filename, expected in item["files"].items():
                with self.subTest(filename=filename):
                    path = MATRIX_DIR / filename
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    self.assertEqual(path.stat().st_size, expected["size"])
                    self.assertEqual(digest, expected["sha256"])

    def test_every_export_is_fully_structured_and_lossless(self) -> None:
        for identifier, program in self.programs.items():
            with self.subTest(case=identifier):
                raw_data = (
                    MATRIX_DIR / self.cases[identifier]["expected_rd"]
                ).read_bytes()
                self.assertEqual(program.issues, [])
                self.assertEqual(len(program.records), 70)
                self.assertEqual(program.encode(), raw_data)
                checksum = command(program, "file_checksum")
                self.assertEqual(
                    checksum.values["value"],
                    program.source_checksum_basis,
                )

    def test_power_boundary_raw_values(self) -> None:
        expected = {
            "m001-power-000": 16,
            "m002-power-001": 164,
            "m003-power-050": 8192,
            "m004-power-099": 16219,
            "m005-power-100": 16383,
        }
        for identifier, value in expected.items():
            with self.subTest(case=identifier):
                item = command(
                    self.programs[identifier],
                    "layer_laser_1_min_power",
                )
                raw = bytes.fromhex(item.raw)
                self.assertEqual(decode_u14(raw[-2:]), value)

    def test_speed_precision(self) -> None:
        decimal = command(
            self.programs["m006-speed-decimal"],
            "layer_speed",
        )
        low = command(
            self.programs["m007-speed-low"],
            "layer_speed",
        )
        self.assertEqual(decimal.values["speed_mm_s"], 12.345)
        self.assertEqual(low.values["speed_mm_s"], 0.1)

    def test_air_assist_changes_layer_control(self) -> None:
        operations = [
            record.values["operation"]
            for record in self.programs["m008-air-on"].records
            if isinstance(record, KnownCommand)
            and record.name == "layer_control"
        ]
        self.assertIn(0x13, operations)
        self.assertNotIn(0x12, operations)

    def test_lone_ui_layer_is_compacted_to_job_layer_zero(self) -> None:
        program = self.programs["m009-layer-1"]
        selected = [
            record.values["layer"]
            for record in program.records
            if isinstance(record, KnownCommand)
            and record.name == "select_layer"
        ]
        color = command(program, "layer_color")
        self.assertEqual(selected, [0])
        self.assertEqual(color.values["layer"], 0)
        self.assertEqual(color.values["color_rgb"], 0x0000FF)

    def test_geometry_coordinates(self) -> None:
        vertical = command(
            self.programs["m010-vertical"],
            "cut_absolute",
        )
        offset_x = command(
            self.programs["m011-offset-x"],
            "move_absolute",
        )
        offset_y = command(
            self.programs["m012-offset-y"],
            "move_absolute",
        )
        self.assertEqual(vertical.values, {"x_mm": 20.0, "y_mm": 30.0})
        self.assertEqual(offset_x.values, {"x_mm": 21.0, "y_mm": 20.0})
        self.assertEqual(offset_y.values, {"x_mm": 20.0, "y_mm": 21.0})


if __name__ == "__main__":
    unittest.main()
