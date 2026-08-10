"""Tests for advanced LightBurn discovery-project generation."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from ruida_re.advanced import BUILDERS, build_mixed_project, generate
from ruida_re.raster_fixture import CASES as RASTER_CASES
from ruida_re.program import KnownCommand, decode


class AdvancedFixtureTest(unittest.TestCase):
    def test_generated_projects_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            manifest_path = directory / "advanced.json"
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            identifiers = {
                item["identifier"] for item in manifest["cases"]
            }
            self.assertEqual(identifiers, set(BUILDERS))
            for item in manifest["cases"]:
                project_path = directory / item["project"]
                self.assertTrue(project_path.is_file())
                self.assertIn(project_path.name, item["files"])
                self.assertNotIn(item["expected_rd"], item["files"])
                expected_status = (
                    "blocked"
                    if item["identifier"] == "a003-negative-coordinate"
                    else "pending"
                )
                self.assertEqual(item["export_status"], expected_status)
                ET.parse(project_path)

    def test_checked_exports_are_content_addressed_and_lossless(self) -> None:
        root = Path(__file__).resolve().parents[1]
        directory = root / "fixtures/lightburn-2.1.03/advanced"
        manifest = json.loads(
            (directory / "advanced.json").read_text(encoding="utf-8")
        )
        status = {
            item["identifier"]: item["export_status"]
            for item in manifest["cases"]
        }
        self.assertEqual(
            status,
            {
                "a001-multilayer": "captured",
                "a002-relative-polyline": "captured",
                "a003-negative-coordinate": "blocked",
                "a004-mixed-vector-raster": "captured",
            },
        )
        for item in manifest["cases"]:
            for name, metadata in item["files"].items():
                path = directory / name
                self.assertEqual(path.stat().st_size, metadata["size"])
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    metadata["sha256"],
                )
        observed = set()
        for path in sorted(directory.glob("*.rd")):
            program = decode(path.read_bytes())
            self.assertEqual(program.issues, [])
            self.assertEqual(program.encode(), path.read_bytes())
            observed.update(
                record.opcode
                for record in program.records
                if isinstance(record, KnownCommand)
            )
        self.assertIn("aa", observed)
        self.assertIn("ab", observed)

        multilayer = decode(
            (directory / "a001-multilayer.rd").read_bytes()
        )
        selected_layers = [
            record.values["layer"]
            for record in multilayer.records
            if isinstance(record, KnownCommand)
            and record.name == "select_layer"
        ]
        self.assertEqual(selected_layers, [0, 1])
        layer_count = next(
            record.values["count_minus_one"]
            for record in multilayer.records
            if isinstance(record, KnownCommand)
            and record.name == "layer_count"
        )
        self.assertEqual(layer_count, 1)

        mixed = decode(
            (directory / "a004-mixed-vector-raster.rd").read_bytes()
        )
        mixed_modes = [
            record.values["value"]
            for record in mixed.records
            if isinstance(record, KnownCommand)
            and record.name == "layer_mode_or_attributes"
        ]
        self.assertEqual(mixed_modes, [0, 1])

        relative = decode(
            (directory / "a002-relative-polyline.rd").read_bytes()
        )
        movements = [
            (record.name, record.values)
            for record in relative.records
            if isinstance(record, KnownCommand)
            and record.name in ("cut_horizontal", "cut_vertical")
        ]
        self.assertEqual(
            movements,
            [
                ("cut_vertical", {"dy_mm": -2.0}),
                ("cut_horizontal", {"dx_mm": -3.0}),
                ("cut_vertical", {"dy_mm": 2.0}),
                ("cut_horizontal", {"dx_mm": 3.0}),
            ],
        )

    def test_multilayer_case_has_two_settings_and_shapes(self) -> None:
        project = BUILDERS["a001-multilayer"][1]()
        settings = project.findall("CutSetting")
        shapes = project.findall("Shape")
        self.assertEqual(len(settings), 2)
        self.assertEqual(len(shapes), 2)
        self.assertEqual(shapes[0].get("CutIndex"), "0")
        self.assertEqual(shapes[1].get("CutIndex"), "1")

    def test_mixed_case_is_deterministic_and_has_two_layers(self) -> None:
        first = build_mixed_project()
        second = build_mixed_project()
        self.assertEqual(ET.tostring(first), ET.tostring(second))
        self.assertEqual(
            [element.tag for element in first if "CutSetting" in element.tag],
            ["CutSetting", "CutSetting_Img"],
        )
        shapes = first.findall("Shape")
        self.assertEqual(len(shapes), 2)
        self.assertEqual(
            [(shape.get("Type"), shape.get("CutIndex")) for shape in shapes],
            [("Path", "0"), ("Bitmap", "1")],
        )
        self.assertEqual(shapes[0].findtext("XForm"), "1 0 0 1 20 20")
        self.assertEqual(shapes[0].findtext("VertList"), "V 0 0 V 10 0")
        self.assertEqual(shapes[1].findtext("XForm"), "1 0 0 1 22 25")
        self.assertEqual((shapes[1].get("W"), shapes[1].get("H")), ("4", "2"))
        image_setting = first.find("CutSetting_Img")
        self.assertIsNotNone(image_setting)
        self.assertEqual(
            image_setting.find("ditherMode").get("Value"),
            "threshold",
        )
        self.assertEqual(image_setting.find("bidir").get("Value"), "0")
        self.assertEqual(image_setting.find("angle").get("Value"), "0")

    def test_mixed_case_uses_controlled_raster_case_values(self) -> None:
        baseline = next(
            case
            for case in RASTER_CASES
            if case.identifier
            == "r001-threshold-horizontal-unidirectional"
        )
        controlled = replace(
            baseline,
            speed_mm_s=73,
            min_power_percent=31,
            max_power_percent=62,
        )
        project = build_mixed_project(controlled)
        setting = project.find("CutSetting_Img")
        self.assertIsNotNone(setting)
        self.assertEqual(setting.find("speed").get("Value"), "73")
        self.assertEqual(setting.find("minPower").get("Value"), "31")
        self.assertEqual(setting.find("maxPower").get("Value"), "62")

    def test_existing_generated_projects_remain_byte_identical(self) -> None:
        expected = {
            "a001-multilayer.lbrn2": (
                "f43aae70b2f92d14ecb1392da6252505036fe6fd198d33814547a11358"
                "f4a812"
            ),
            "a002-relative-polyline.lbrn2": (
                "19b5567379fc54abffd9c7b9b8680eefbcebbfd80f488880f9ff393511"
                "197e8d"
            ),
            "a003-negative-coordinate.lbrn2": (
                "a8c359b4e7c6d93c09e08fcdaf09f8eb806e5bd9200250c3f6ee9ae2"
                "5c139496"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            observed = {
                name: hashlib.sha256(
                    (directory / name).read_bytes()
                ).hexdigest()
                for name in expected
            }
        self.assertEqual(observed, expected)

    def test_generation_is_no_clobber_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            with self.assertRaises(FileExistsError):
                generate(directory)
            with redirect_stdout(io.StringIO()):
                generate(directory, force=True)

    def test_relative_case_has_ten_segments(self) -> None:
        project = BUILDERS["a002-relative-polyline"][1]()
        shape = project.find("Shape")
        self.assertIsNotNone(shape)
        prim_list = shape.find("PrimList")
        self.assertIsNotNone(prim_list)
        self.assertEqual(prim_list.text.count("L "), 10)

    def test_negative_case_crosses_x_zero(self) -> None:
        project = BUILDERS["a003-negative-coordinate"][1]()
        shape = project.find("Shape")
        self.assertIsNotNone(shape)
        transform = shape.find("XForm")
        self.assertIsNotNone(transform)
        self.assertTrue(transform.text.endswith("-1 20"))
        vertices = shape.find("VertList")
        self.assertIsNotNone(vertices)
        self.assertEqual(vertices.text, "V 0 0 V 2 0")


if __name__ == "__main__":
    unittest.main()
