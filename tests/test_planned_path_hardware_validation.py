"""Offline verification of planned-path Ruida hardware evidence."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

from ruida_re.api import RuidaCodec
from ruida_re.job import (
    LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH,
    Bounds,
    JobPlan,
    LayerPlan,
    MarkTo,
    RasterSection,
    RuidaJobCompiler,
    TravelTo,
)
from ruida_re.program import KnownCommand

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/hardware/ruida-644xs-usb-serial-planned-path-v1"
MANIFEST_PATH = FIXTURE / "manifest-v1.json"
EXPECTED_JOBS = {
    "planned-path-45-10pct": (
        "1c28e301321fd54bf8e6bc54e7c9b370305fb601c0d1cea5ef4d9976124d953d",
        24757,
    ),
    "planned-path-45-15pct": (
        "92c328e7fc9d98ba38da1e8179560d2f2675ac79983b4efeef11889bb8bb1123",
        24197,
    ),
}
POWER_COMMANDS = (
    "layer_laser_1_min_power",
    "layer_laser_1_max_power",
    "layer_laser_2_min_power",
    "layer_laser_2_max_power",
    "laser_1_min_power",
    "laser_1_max_power",
    "laser_2_min_power",
    "laser_2_max_power",
)


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _event(item: dict[str, Any]) -> TravelTo | MarkTo:
    event_type = item["type"]
    if event_type == "travel_to":
        return TravelTo(item["x_mm"], item["y_mm"])
    if event_type == "mark_to":
        return MarkTo(item["x_mm"], item["y_mm"])
    raise ValueError(f"Unknown planned-path hardware event: {event_type}")


def _plan(manifest: dict[str, Any], power: float) -> JobPlan:
    layer = manifest["plan"]["layer"]
    sections = tuple(
        RasterSection(tuple(_event(event) for event in section["events"]))
        for section in layer["raster_sections"]
    )
    return JobPlan(
        layers=(
            LayerPlan(
                index=layer["index"],
                speed_mm_s=layer["speed_mm_s"],
                min_power_percent=power,
                max_power_percent=power,
                events=(),
                kind=layer["kind"],
                air_assist=layer["air_assist"],
                color_rgb=layer["color_rgb"],
                laser_index=layer["laser_index"],
                raster_processing=layer["raster_processing"],
                raster_sections=sections,
            ),
        )
    )


def _known(path: Path) -> tuple[KnownCommand, ...]:
    program = RuidaCodec(context="job").decode(
        path.read_bytes(),
        container="rd",
    )
    return tuple(
        record for record in program.records if isinstance(record, KnownCommand)
    )


class PlannedPathHardwareValidationTest(unittest.TestCase):
    def test_manifest_and_jobs_are_content_addressed(self) -> None:
        manifest = _manifest()

        self.assertEqual(
            manifest["schema"],
            "ruida-re.hardware-planned-path-validation.v1",
        )
        self.assertEqual(
            manifest["identifier"],
            "hardware-ruida-644xs-usb-serial-planned-path-v1",
        )
        self.assertEqual(
            manifest["generating_revision"],
            "7ef0ff5011bd0684a2a70cb72c43e666f9438651",
        )
        for job in manifest["jobs"]:
            with self.subTest(job=job["identifier"]):
                expected_hash, expected_checksum = EXPECTED_JOBS[job["identifier"]]
                data = (FIXTURE / job["file"]).read_bytes()
                self.assertEqual(job["size_bytes"], 574)
                self.assertEqual(len(data), 574)
                self.assertEqual(job["sha256"], expected_hash)
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(),
                    expected_hash,
                )
                self.assertEqual(job["checksum"]["value"], expected_checksum)

        serialized = json.dumps(manifest).lower()
        for private_path in (
            "/dev/",
            "/tmp/",
            "/users/",
            "cu.usb",
            "tty.usb",
            "usbserial",
            "usbmodem",
        ):
            self.assertNotIn(private_path, serialized)

    def test_jobs_are_known_and_recompile_byte_exactly(self) -> None:
        manifest = _manifest()
        compiler = RuidaJobCompiler(LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH)

        for job in manifest["jobs"]:
            with self.subTest(job=job["identifier"]):
                data = (FIXTURE / job["file"]).read_bytes()
                codec = RuidaCodec(context="job")
                program = codec.decode(data, container="rd")
                checksum = next(
                    record.values["value"]
                    for record in program.records
                    if isinstance(record, KnownCommand)
                    and record.name == "file_checksum"
                )

                self.assertEqual(program.issues, [])
                self.assertEqual(len(program.records), 77)
                self.assertTrue(
                    all(isinstance(record, KnownCommand) for record in program.records)
                )
                self.assertEqual(job["decode"]["known_records"], 77)
                self.assertEqual(job["decode"]["opaque_records"], 0)
                self.assertEqual(job["decode"]["issues"], [])
                self.assertEqual(checksum, job["checksum"]["value"])
                self.assertEqual(checksum, program.source_checksum_basis)
                self.assertEqual(codec.encode(program), data)
                self.assertEqual(
                    codec.encode(program, checksum_policy="recompute"),
                    data,
                )

                result = compiler.compile(
                    _plan(manifest, job["requested_power_percent"])
                )
                self.assertEqual(result.encode_rd(), data)
                self.assertEqual(
                    result.bounds,
                    Bounds(20, 20, 32, 40),
                )
                self.assertAlmostEqual(
                    result.marked_distance_mm,
                    manifest["plan"]["marked_distance_mm"],
                )

    def test_motion_is_exact_and_pair_diff_is_power_only(self) -> None:
        manifest = _manifest()
        paths = [FIXTURE / job["file"] for job in manifest["jobs"]]
        first, second = (_known(path) for path in paths)
        names = [record.name for record in first]

        self.assertEqual(names, [record.name for record in second])
        self.assertEqual(names.count("move_absolute"), 5)
        self.assertEqual(names.count("cut_absolute"), 5)
        self.assertNotIn("move_relative", names)
        self.assertNotIn("cut_relative", names)
        self.assertNotIn("z_offset_delta", names)

        changed = [
            after.name
            for before, after in zip(first, second, strict=True)
            if before.values != after.values
        ]
        self.assertEqual(changed, [*POWER_COMMANDS, "file_checksum"])

    def test_receipts_and_observations_have_bounded_meaning(self) -> None:
        manifest = _manifest()
        transmissions = manifest["transmissions"]

        self.assertEqual(
            [item["operator_observation"]["status"] for item in transmissions],
            [
                "motion-observed-marking-inconclusive",
                "operator-reported-success",
            ],
        )
        for item in transmissions:
            with self.subTest(sequence=item["sequence"]):
                receipt = item["transport_receipt"]
                self.assertEqual(receipt["packets"], 1)
                self.assertEqual(receipt["completed_packets"], 1)
                self.assertEqual(receipt["packet_bytes"], 574)
                self.assertEqual(receipt["transmissions"], 1)
                self.assertEqual(receipt["retries"], 0)
                self.assertFalse(receipt["controller_acknowledgement"])
                self.assertFalse(receipt["execution_acknowledgement"])

        self.assertEqual(
            manifest["result"]["status"],
            "operator-reported-success",
        )
        self.assertEqual(
            manifest["result"]["scoped_execution_evidence"],
            "operator-observed",
        )
        self.assertEqual(
            manifest["result"]["mode_wide_execution_evidence"],
            "not-observed",
        )
        self.assertEqual(
            manifest["result"]["default_profile_promotion"],
            "withheld",
        )
        profile = LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
        self.assertEqual(profile.execution_evidence, "not-observed")
        self.assertIsNone(profile.execution_evidence_source)
        self.assertIsNotNone(profile.planned_path_raster_mode)
        assert profile.planned_path_raster_mode is not None
        self.assertEqual(
            profile.planned_path_raster_mode.execution_evidence,
            "not-observed",
        )


if __name__ == "__main__":
    unittest.main()
