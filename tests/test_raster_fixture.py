"""Tests for controlled LightBurn raster discovery projects."""

from __future__ import annotations

import base64
from contextlib import redirect_stdout
from dataclasses import asdict
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zlib

from ruida_re.raster_fixture import (
    BINARY_PIXELS,
    CASES,
    GRAYSCALE_PIXELS,
    PNG_SIGNATURE,
    RasterCase,
    build_project,
    encode_grayscale_png,
    generate,
    record,
)


def _decode_grayscale_png(
    data: bytes,
) -> tuple[tuple[int, ...], ...]:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Not a PNG")
    position = len(PNG_SIGNATURE)
    width = 0
    height = 0
    compressed = bytearray()
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        content_start = position + 8
        content_end = content_start + length
        content = data[content_start:content_end]
        checksum = struct.unpack(">I", data[content_end : content_end + 4])[0]
        expected = zlib.crc32(kind + content) & 0xFFFFFFFF
        if checksum != expected:
            raise ValueError("Invalid PNG checksum")
        if kind == b"IHDR":
            width, height, depth, color, _, _, _ = struct.unpack(
                ">IIBBBBB", content
            )
            if depth != 8 or color != 0:
                raise ValueError("PNG is not 8-bit grayscale")
        elif kind == b"IDAT":
            compressed.extend(content)
        elif kind == b"IEND":
            break
        position = content_end + 4
    scanlines = zlib.decompress(compressed)
    stride = width + 1
    if len(scanlines) != height * stride:
        raise ValueError("Invalid PNG scanline length")
    rows = []
    for index in range(height):
        row = scanlines[index * stride : (index + 1) * stride]
        if row[0] != 0:
            raise ValueError("Unexpected PNG filter")
        rows.append(tuple(row[1:]))
    return tuple(rows)


def _setting_values(project: ET.Element) -> dict[str, str]:
    setting = project.find("CutSetting_Img")
    if setting is None:
        raise AssertionError("Missing image cut setting")
    return {child.tag: child.get("Value", "") for child in setting}


def _controlled_differences(
    first: RasterCase,
    second: RasterCase,
) -> set[str]:
    first_values = asdict(first)
    second_values = asdict(second)
    ignored = {"identifier", "purpose"}
    return {
        name
        for name in first_values
        if name not in ignored
        and first_values[name] != second_values[name]
    }


class RasterFixtureTest(unittest.TestCase):
    def test_case_matrix_has_controlled_differences(self) -> None:
        by_id = {case.identifier: case for case in CASES}
        self.assertEqual(
            tuple(by_id),
            (
                "r001-threshold-horizontal-unidirectional",
                "r002-threshold-horizontal-bidirectional",
                "r003-threshold-vertical-unidirectional",
                "r004-threshold-interval-025",
                "r005-grayscale-range-10-90",
                "r006-grayscale-range-30-90",
                "r007-3d-slice-four-pass",
                "r008-threshold-vertical-bidirectional",
                "r009-3d-slice-four-pass-z-step-05",
            ),
        )
        baseline = by_id[
            "r001-threshold-horizontal-unidirectional"
        ]
        self.assertEqual(
            _controlled_differences(
                baseline,
                by_id["r002-threshold-horizontal-bidirectional"],
            ),
            {"bidirectional"},
        )
        self.assertEqual(
            _controlled_differences(
                baseline,
                by_id["r003-threshold-vertical-unidirectional"],
            ),
            {"angle_degrees"},
        )
        self.assertEqual(
            _controlled_differences(
                baseline,
                by_id["r004-threshold-interval-025"],
            ),
            {"interval_mm"},
        )
        self.assertFalse(baseline.bidirectional)
        self.assertTrue(
            by_id["r002-threshold-horizontal-bidirectional"]
            .bidirectional
        )
        self.assertEqual(
            by_id["r003-threshold-vertical-unidirectional"]
            .angle_degrees,
            90,
        )
        self.assertEqual(
            by_id["r004-threshold-interval-025"].interval_mm,
            0.25,
        )
        grayscale = by_id["r005-grayscale-range-10-90"]
        minimum = by_id["r006-grayscale-range-30-90"]
        self.assertEqual(
            _controlled_differences(grayscale, minimum),
            {"min_power_percent"},
        )
        self.assertEqual(grayscale.pixels, minimum.pixels)
        self.assertEqual(grayscale.max_power_percent, 90)
        self.assertEqual(minimum.max_power_percent, 90)
        self.assertEqual(grayscale.min_power_percent, 10)
        self.assertEqual(minimum.min_power_percent, 30)
        sliced = by_id["r007-3d-slice-four-pass"]
        self.assertEqual(sliced.dither_mode, "3dslice")
        self.assertEqual(sliced.num_passes, 4)
        vertical = by_id["r003-threshold-vertical-unidirectional"]
        vertical_bidir = by_id[
            "r008-threshold-vertical-bidirectional"
        ]
        self.assertEqual(
            _controlled_differences(vertical, vertical_bidir),
            {"bidirectional"},
        )
        sliced_z = by_id["r009-3d-slice-four-pass-z-step-05"]
        self.assertEqual(
            _controlled_differences(sliced, sliced_z),
            {"z_per_pass_mm"},
        )
        self.assertEqual(sliced_z.z_per_pass_mm, 0.5)

    def test_embedded_pngs_preserve_exact_pixels(self) -> None:
        for case in CASES:
            with self.subTest(case=case.identifier):
                project = build_project(case)
                shape = project.find("Shape")
                self.assertIsNotNone(shape)
                encoded = shape.get("Data")
                self.assertIsNotNone(encoded)
                png = base64.b64decode(encoded, validate=True)
                self.assertEqual(
                    _decode_grayscale_png(png),
                    case.pixels,
                )

    def test_project_geometry_and_layer_settings(self) -> None:
        for case in CASES:
            with self.subTest(case=case.identifier):
                project = build_project(case)
                settings = _setting_values(project)
                self.assertEqual(settings["ditherMode"], case.dither_mode)
                self.assertEqual(
                    settings["bidir"], str(int(case.bidirectional))
                )
                self.assertEqual(
                    float(settings["interval"]), case.interval_mm
                )
                self.assertEqual(
                    float(settings["dpi"]), 25.4 / case.interval_mm
                )
                self.assertEqual(
                    int(settings["numPasses"]), case.num_passes
                )
                self.assertEqual(
                    float(settings["zPerPass"]), case.z_per_pass_mm
                )
                self.assertEqual(settings["overscan"], "0")
                shape = project.find("Shape")
                self.assertIsNotNone(shape)
                self.assertEqual(shape.get("Type"), "Bitmap")
                self.assertEqual(float(shape.get("W", "0")), case.width_mm)
                self.assertEqual(
                    float(shape.get("H", "0")), case.height_mm
                )
                transform = shape.find("XForm")
                self.assertIsNotNone(transform)
                self.assertEqual(
                    transform.text,
                    (
                        "1 0 0 1 "
                        f"{20 + case.width_mm / 2:g} "
                        f"{20 + case.height_mm / 2:g}"
                    ),
                )

    def test_generate_writes_pending_content_addressed_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            manifest = json.loads(
                (directory / "raster.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest["cases"]), len(CASES))
            for case, item in zip(CASES, manifest["cases"], strict=True):
                with self.subTest(case=case.identifier):
                    self.assertEqual(item["identifier"], case.identifier)
                    self.assertEqual(item["export_status"], "pending")
                    self.assertEqual(
                        item["layer"]["z_per_pass_mm"],
                        case.z_per_pass_mm,
                    )
                    self.assertNotIn(item["expected_rd"], item["files"])
                    project_path = directory / item["project"]
                    self.assertTrue(project_path.is_file())
                    self.assertIn(project_path.name, item["files"])
                    ET.parse(project_path)

    def test_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first_temporary:
            with tempfile.TemporaryDirectory() as second_temporary:
                first = Path(first_temporary)
                second = Path(second_temporary)
                with redirect_stdout(io.StringIO()):
                    generate(first)
                    generate(second)
                for case in CASES:
                    name = f"{case.identifier}.lbrn2"
                    self.assertEqual(
                        (first / name).read_bytes(),
                        (second / name).read_bytes(),
                    )
                self.assertEqual(
                    (first / "raster.json").read_bytes(),
                    (second / "raster.json").read_bytes(),
                )

    def test_record_requires_and_hashes_every_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            with self.assertRaises(FileNotFoundError):
                record(directory)
            for case in CASES:
                (directory / f"{case.identifier}.rd").write_bytes(b"rd")
            with redirect_stdout(io.StringIO()):
                record(directory)
            manifest = json.loads(
                (directory / "raster.json").read_text(encoding="utf-8")
            )
            for item in manifest["cases"]:
                self.assertEqual(item["export_status"], "captured")
                self.assertIn(item["expected_rd"], item["files"])

    def test_generation_is_no_clobber_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            with self.assertRaises(FileExistsError):
                generate(directory)
            with redirect_stdout(io.StringIO()):
                generate(directory, force=True)

    def test_png_validation(self) -> None:
        self.assertEqual(
            _decode_grayscale_png(encode_grayscale_png(BINARY_PIXELS)),
            BINARY_PIXELS,
        )
        self.assertEqual(
            _decode_grayscale_png(encode_grayscale_png(GRAYSCALE_PIXELS)),
            GRAYSCALE_PIXELS,
        )
        with self.assertRaises(ValueError):
            encode_grayscale_png(())
        with self.assertRaises(ValueError):
            encode_grayscale_png(((0, 1), (2,)))
        with self.assertRaises(ValueError):
            encode_grayscale_png(((256,),))


if __name__ == "__main__":
    unittest.main()
