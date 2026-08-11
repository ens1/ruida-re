"""Controlled protocol evidence from LightBurn raster exports."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from ruida_re.program import KnownCommand, Program, decode


ROOT = Path(__file__).resolve().parents[1]
RASTER_DIR = ROOT / "fixtures/lightburn-2.1.03/raster"
MANIFEST_PATH = RASTER_DIR / "raster.json"


def commands(program: Program, name: str) -> list[KnownCommand]:
    return [
        record
        for record in program.records
        if isinstance(record, KnownCommand) and record.name == name
    ]


def marked_distance(program: Program) -> float:
    distances = []
    for record in program.records:
        if not isinstance(record, KnownCommand):
            continue
        if record.name == "cut_horizontal":
            distances.append(abs(record.values["dx_mm"]))
        elif record.name == "cut_vertical":
            distances.append(abs(record.values["dy_mm"]))
        elif record.name == "cut_relative":
            distances.append(
                math.hypot(
                    record.values["dx_mm"],
                    record.values["dy_mm"],
                )
            )
        elif record.name.startswith("cut_"):
            raise AssertionError(
                f"Unsupported marked motion: {record.name}"
            )
    return math.fsum(distances)


class RasterProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.cases = {item["identifier"]: item for item in manifest["cases"]}
        cls.raw = {
            identifier: (RASTER_DIR / item["expected_rd"]).read_bytes()
            for identifier, item in cls.cases.items()
        }
        cls.programs = {
            identifier: decode(raw)
            for identifier, raw in cls.raw.items()
        }

    def test_every_export_is_fully_structured_and_lossless(self) -> None:
        manifest_files = {
            item["expected_rd"] for item in self.cases.values()
        }
        captured_files = {path.name for path in RASTER_DIR.glob("*.rd")}
        self.assertEqual(manifest_files, captured_files)
        for identifier, program in self.programs.items():
            with self.subTest(case=identifier):
                self.assertEqual(program.issues, [])
                self.assertTrue(
                    all(
                        isinstance(record, KnownCommand)
                        for record in program.records
                    )
                )
                self.assertEqual(program.encode(), self.raw[identifier])

    def test_scan_strategy_metadata(self) -> None:
        expected = {
            "r001-threshold-horizontal-unidirectional": (1, 2),
            "r002-threshold-horizontal-bidirectional": (2, 1),
            "r003-threshold-vertical-unidirectional": (3, 4),
            "r008-threshold-vertical-bidirectional": (4, 3),
        }
        for identifier, (mode_value, operation) in expected.items():
            with self.subTest(case=identifier):
                program = self.programs[identifier]
                mode = commands(program, "layer_mode_or_attributes")
                controls = commands(program, "layer_control")
                self.assertEqual(len(mode), 1)
                self.assertGreater(len(controls), 0)
                self.assertEqual(mode[0].opcode, "ca41")
                self.assertEqual(mode[0].values, {
                    "layer": 0,
                    "value": mode_value,
                })
                self.assertEqual(mode[0].raw, f"ca4100{mode_value:02x}")
                self.assertEqual(controls[0].opcode, "ca01")
                self.assertEqual(
                    controls[0].values["operation"],
                    operation,
                )
                self.assertEqual(controls[0].raw, f"ca01{operation:02x}")

    def test_grayscale_modulation_is_normalized_within_power_range(
        self,
    ) -> None:
        identifiers = (
            "r005-grayscale-range-10-90",
            "r006-grayscale-range-30-90",
        )
        modulation_names = {"immediate_power_1", "immediate_power_3"}
        modulation = []
        for identifier in identifiers:
            records = [
                record
                for record in self.programs[identifier].records
                if isinstance(record, KnownCommand)
                and record.name in modulation_names
            ]
            modulation.append(
                [
                    (record.name, record.values, record.raw)
                    for record in records
                ]
            )
            self.assertEqual(len(records), 14)
            for first, third in zip(records[::2], records[1::2]):
                self.assertEqual(first.name, "immediate_power_1")
                self.assertEqual(third.name, "immediate_power_3")
                self.assertEqual(first.opcode, "c7")
                self.assertEqual(third.opcode, "c2")
                self.assertEqual(first.values, third.values)
                self.assertEqual(first.raw[2:], third.raw[2:])
        self.assertEqual(modulation[0], modulation[1])

        minimum_names = (
            "layer_laser_1_min_power",
            "layer_laser_2_min_power",
            "laser_1_min_power",
            "laser_2_min_power",
        )
        maximum_names = (
            "layer_laser_1_max_power",
            "layer_laser_2_max_power",
            "laser_1_max_power",
            "laser_2_max_power",
        )
        minimums = [
            {
                name: commands(self.programs[identifier], name)[0].values[
                    "power_percent"
                ]
                for name in minimum_names
            }
            for identifier in identifiers
        ]
        maximums = [
            {
                name: commands(self.programs[identifier], name)[0].values[
                    "power_percent"
                ]
                for name in maximum_names
            }
            for identifier in identifiers
        ]
        self.assertNotEqual(minimums[0], minimums[1])
        self.assertEqual(len(set(minimums[0].values())), 1)
        self.assertEqual(len(set(minimums[1].values())), 1)
        self.assertAlmostEqual(next(iter(minimums[0].values())), 10.0, 2)
        self.assertAlmostEqual(next(iter(minimums[1].values())), 30.0, 2)
        self.assertEqual(maximums[0], maximums[1])

        normalized = [
            record.values["power_percent"]
            for record in commands(
                self.programs[identifiers[0]],
                "immediate_power_1",
            )
        ]
        self.assertLess(min(normalized), 30)
        effective = []
        for minimum in (10, 30):
            effective.append(
                [
                    minimum + value / 100 * (90 - minimum)
                    for value in normalized
                ]
            )
        self.assertNotEqual(effective[0], effective[1])
        self.assertTrue(all(10 <= value <= 90 for value in effective[0]))
        self.assertTrue(all(30 <= value <= 90 for value in effective[1]))

    def test_z_per_pass_does_not_change_exported_rd(self) -> None:
        self.assertEqual(
            self.raw["r007-3d-slice-four-pass"],
            self.raw["r009-3d-slice-four-pass-z-step-05"],
        )

    def test_address_800_is_truncated_marked_distance(self) -> None:
        expected_distances = {
            "r001-threshold-horizontal-unidirectional": 7.0,
            "r003-threshold-vertical-unidirectional": 5.5,
            "r004-threshold-interval-025": 13.5,
            "r005-grayscale-range-10-90": 3.5,
            "r007-3d-slice-four-pass": 14.0,
        }
        for identifier, expected_distance in expected_distances.items():
            with self.subTest(case=identifier):
                program = self.programs[identifier]
                distance = marked_distance(program)
                settings = [
                    record
                    for record in commands(program, "set_setting")
                    if record.values["address"] == 800
                ]
                self.assertEqual(distance, expected_distance)
                self.assertEqual(len(settings), 1)
                metric = settings[0].values["first_value"]
                self.assertEqual(
                    settings[0].values["second_value"],
                    metric,
                )
                self.assertEqual(metric, math.floor(distance))
                self.assertEqual(metric, math.trunc(distance))


if __name__ == "__main__":
    unittest.main()
