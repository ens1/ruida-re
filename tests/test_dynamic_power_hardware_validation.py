"""Offline verification of scoped dynamic-power hardware evidence."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from ruida_re.api import RuidaCodec
from ruida_re.job import (
    LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH,
    JobPlan,
    LaserChannelPlan,
    LayerEvent,
    LayerPlan,
    MarkTo,
    MarkWithCurrentPower,
    MarkWithPower,
    RuidaJobCompiler,
    TravelTo,
)
from ruida_re.program import KnownCommand


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "fixtures/hardware"
    / "boss-ls2040-usb-serial-rayforge-dynamic-vector-v1"
)
MANIFEST_PATH = FIXTURE / "manifest-v1.json"
EXPECTED_JOBS = {
    "dynamic-vector-15-10-15-v1": (
        "ec6a24b47bac882e62fa3ac996727e3b452b81b7717e3137a38809501d851809",
        25057,
    ),
    "dynamic-vector-15-5-15-v2": (
        "723f5f8de65db05717ac95d9c7d11774dab9af558338c4b8daa486afb95f129b",
        26787,
    ),
}


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _channels(
    minimum: float,
    maximum: float,
) -> tuple[LaserChannelPlan, ...]:
    return (
        LaserChannelPlan(1, True, minimum, maximum),
        LaserChannelPlan(2, False, 40, 40),
    )


def _plan(job: dict[str, Any]) -> JobPlan:
    baseline = job["layer_laser_1_power_percent"]
    reduced = job["reduced_laser_1_power_percent"]
    reduced_channels = _channels(reduced["minimum"], reduced["maximum"])
    events: list[LayerEvent] = []
    for item in job["motion"]:
        coordinates = (item["x_mm"], item["y_mm"])
        if item["type"] == "travel_to":
            event: LayerEvent = TravelTo(*coordinates)
        elif item["type"] == "mark_to":
            event = MarkTo(*coordinates)
        elif item["type"] == "mark_with_power":
            event = MarkWithPower(*coordinates, reduced_channels)
        elif item["type"] == "mark_with_current_power":
            event = MarkWithCurrentPower(*coordinates)
        else:
            raise ValueError(f"Unknown hardware event: {item['type']}")
        events.append(event)
    return JobPlan(
        layers=(
            LayerPlan(
                index=0,
                speed_mm_s=job["speed_mm_s"],
                min_power_percent=baseline["minimum"],
                max_power_percent=baseline["maximum"],
                events=tuple(events),
                color_rgb=job["color_rgb"],
                laser_channels=_channels(
                    baseline["minimum"],
                    baseline["maximum"],
                ),
            ),
        )
    )


def _known(data: bytes) -> tuple[KnownCommand, ...]:
    program = RuidaCodec(context="job").decode(data, container="rd")
    return tuple(
        record
        for record in program.records
        if isinstance(record, KnownCommand)
    )


class DynamicPowerHardwareValidationTest(unittest.TestCase):
    def test_manifest_and_jobs_are_content_addressed(self) -> None:
        manifest = _manifest()

        self.assertEqual(
            manifest["schema"],
            "ruida-re.hardware-dynamic-power-validation.v1",
        )
        self.assertEqual(
            manifest["generating_revisions"]["ruida_re"],
            "7ef0ff5011bd0684a2a70cb72c43e666f9438651",
        )
        for job in manifest["jobs"]:
            with self.subTest(job=job["identifier"]):
                expected_hash, expected_checksum = EXPECTED_JOBS[
                    job["identifier"]
                ]
                data = (FIXTURE / job["file"]).read_bytes()
                self.assertEqual(len(data), 539)
                self.assertEqual(job["size_bytes"], 539)
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(),
                    expected_hash,
                )
                self.assertEqual(job["sha256"], expected_hash)
                self.assertEqual(job["checksum"], expected_checksum)

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

    def test_executed_jobs_round_trip_and_recompile_exactly(self) -> None:
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        for job in _manifest()["jobs"]:
            with self.subTest(job=job["identifier"]):
                data = (FIXTURE / job["file"]).read_bytes()
                codec = RuidaCodec(context="job")
                program = codec.decode(data, container="rd")
                records = _known(data)
                checksum = next(
                    record.values["value"]
                    for record in records
                    if record.name == "file_checksum"
                )

                self.assertEqual(program.issues, [])
                self.assertEqual(len(program.records), 79)
                self.assertEqual(len(records), 79)
                self.assertEqual(job["known_records"], 79)
                self.assertEqual(checksum, job["checksum"])
                self.assertEqual(checksum, program.source_checksum_basis)
                self.assertEqual(codec.encode(program), data)
                self.assertEqual(
                    codec.encode(program, checksum_policy="recompute"),
                    data,
                )
                self.assertEqual(
                    compiler.compile(_plan(job)).encode_rd(),
                    data,
                )

    def test_executed_jobs_contain_one_persistent_power_envelope(self) -> None:
        for job in _manifest()["jobs"]:
            with self.subTest(job=job["identifier"]):
                data = (FIXTURE / job["file"]).read_bytes()
                records = _known(data)
                motion = tuple(
                    record.name
                    for record in records
                    if record.name in {"move_absolute", "cut_absolute"}
                )
                dynamic_controls = tuple(
                    index
                    for index, record in enumerate(records)
                    if record.name == "layer_control"
                    and record.values == {"operation": 5}
                )

                self.assertEqual(
                    motion,
                    (
                        "move_absolute",
                        "cut_absolute",
                        "cut_absolute",
                        "cut_absolute",
                    ),
                )
                self.assertEqual(len(dynamic_controls), 1)
                envelope = records[
                    dynamic_controls[0] : dynamic_controls[0] + 7
                ]
                self.assertEqual(
                    tuple(record.name for record in envelope),
                    (
                        "layer_control",
                        "select_layer",
                        "laser_1_min_power",
                        "laser_1_max_power",
                        "laser_2_min_power",
                        "laser_2_max_power",
                        "external_io",
                    ),
                )

    def test_normal_mark_adds_a_baseline_restore_envelope(self) -> None:
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        for job in _manifest()["jobs"]:
            plan = _plan(job)
            layer = plan.layers[0]
            final_event = layer.events[-1]
            self.assertIsInstance(final_event, MarkWithCurrentPower)
            assert isinstance(final_event, MarkWithCurrentPower)
            corrected_layer = replace(
                layer,
                events=(
                    *layer.events[:-1],
                    MarkTo(
                        final_event.x_mm,
                        final_event.y_mm,
                    ),
                ),
            )
            result = compiler.compile(JobPlan((corrected_layer,)))
            records = tuple(
                record
                for record in result.program.records
                if isinstance(record, KnownCommand)
            )
            dynamic_controls = tuple(
                index
                for index, record in enumerate(records)
                if record.name == "layer_control"
                and record.values == {"operation": 5}
            )
            restore = records[
                dynamic_controls[1] : dynamic_controls[1] + 7
            ]

            with self.subTest(job=job["identifier"]):
                self.assertEqual(len(dynamic_controls), 2)
                self.assertEqual(
                    restore[3].values["power_percent"],
                    job["layer_laser_1_power_percent"]["maximum"],
                )
                self.assertEqual(restore[6].values, {"value": 0})

    def test_observation_contradicts_automatic_restore_only(self) -> None:
        manifest = _manifest()
        second = manifest["jobs"][1]["operator_observation"]

        self.assertEqual(
            second["reported_verbatim"],
            [
                "Motion was good, first 30mm was good, no second 30mm",
                "only the first 30mm",
            ],
        )
        self.assertEqual(
            second["automatic_baseline_restore_evidence"],
            "contradicted",
        )
        self.assertEqual(
            manifest["result"]["mode_wide_execution_evidence"],
            "not-observed",
        )
        profile = LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        self.assertEqual(profile.execution_evidence, "not-observed")
        self.assertIsNone(profile.execution_evidence_source)


if __name__ == "__main__":
    unittest.main()
