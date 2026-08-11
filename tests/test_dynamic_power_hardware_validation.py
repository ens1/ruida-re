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
    "dynamic-vector-15-10-15-v1": {
        "sha256": (
            "ec6a24b47bac882e62fa3ac996727e3b452b81b7717e3137a38809501d851809"
        ),
        "size_bytes": 539,
        "checksum": 25057,
        "known_records": 79,
        "wire_power_envelopes": (
            (
                9.99816883354697,
                9.99816883354697,
                39.99877922236465,
                39.99877922236465,
            ),
        ),
    },
    "dynamic-vector-15-5-15-v2": {
        "sha256": (
            "723f5f8de65db05717ac95d9c7d11774dab9af558338c4b8daa486afb95f129b"
        ),
        "size_bytes": 539,
        "checksum": 26787,
        "known_records": 79,
        "wire_power_envelopes": (
            (
                4.999084416773485,
                4.999084416773485,
                39.99877922236465,
                39.99877922236465,
            ),
        ),
    },
    "dynamic-vector-15-5-15-restore-v3": {
        "sha256": (
            "99cfcddb7dfde003f1eccffeaa9b5dcdac24da3dcb5342c2ac13cd4497a5a1f4"
        ),
        "size_bytes": 564,
        "checksum": 27344,
        "known_records": 86,
        "wire_power_envelopes": (
            (
                4.999084416773485,
                4.999084416773485,
                39.99877922236465,
                39.99877922236465,
            ),
            (
                4.999084416773485,
                14.997253250320455,
                39.99877922236465,
                39.99877922236465,
            ),
        ),
    },
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
            manifest["generating_revisions"]["v1_v2"]["ruida_re"],
            "7ef0ff5011bd0684a2a70cb72c43e666f9438651",
        )
        self.assertEqual(
            manifest["generating_revisions"]["v3"]["ruida_re"],
            "a97a8f4e13fb8da39bc8f009a2de310ab26a478b",
        )
        for job in manifest["jobs"]:
            with self.subTest(job=job["identifier"]):
                expected = EXPECTED_JOBS[job["identifier"]]
                data = (FIXTURE / job["file"]).read_bytes()
                self.assertEqual(len(data), expected["size_bytes"])
                self.assertEqual(
                    job["size_bytes"],
                    expected["size_bytes"],
                )
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(),
                    expected["sha256"],
                )
                self.assertEqual(job["sha256"], expected["sha256"])
                self.assertEqual(job["checksum"], expected["checksum"])
                self.assertEqual(
                    job["known_records"],
                    expected["known_records"],
                )

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
                self.assertEqual(
                    len(program.records),
                    job["known_records"],
                )
                self.assertEqual(len(records), job["known_records"])
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

    def test_executed_jobs_contain_declared_power_envelopes(self) -> None:
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
                self.assertEqual(
                    len(dynamic_controls),
                    1 + job["explicit_baseline_restore_envelopes"],
                )
                wire_envelopes = []
                for index in dynamic_controls:
                    envelope = records[index : index + 7]
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
                    wire_envelopes.append(
                        tuple(
                            record.values["power_percent"]
                            for record in envelope[2:6]
                        )
                    )
                self.assertEqual(
                    tuple(wire_envelopes),
                    EXPECTED_JOBS[job["identifier"]][
                        "wire_power_envelopes"
                    ],
                )

    def test_normal_mark_adds_a_baseline_restore_envelope(self) -> None:
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        for job in _manifest()["jobs"][:2]:
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

    def test_corrected_job_diff_is_one_restore_envelope(self) -> None:
        job = _manifest()["jobs"][2]
        corrected_data = (FIXTURE / job["file"]).read_bytes()
        corrected_plan = _plan(job)
        layer = corrected_plan.layers[0]
        final_event = layer.events[-1]
        self.assertIsInstance(final_event, MarkTo)
        assert isinstance(final_event, MarkTo)
        uncorrected_layer = replace(
            layer,
            events=(
                *layer.events[:-1],
                MarkWithCurrentPower(final_event.x_mm, final_event.y_mm),
            ),
        )
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        uncorrected_data = compiler.compile(
            JobPlan((uncorrected_layer,))
        ).encode_rd()

        self.assertEqual(len(corrected_data) - len(uncorrected_data), 25)
        self.assertEqual(
            hashlib.sha256(uncorrected_data).hexdigest(),
            "f562b846d0cc642fc1af17d9f1dfb281f96763b9d463c27e39e7ccd9f19e7e3b",
        )

        corrected = list(_known(corrected_data))
        uncorrected = list(_known(uncorrected_data))
        dynamic_controls = tuple(
            index
            for index, record in enumerate(corrected)
            if record.name == "layer_control"
            and record.values == {"operation": 5}
        )
        self.assertEqual(len(dynamic_controls), 2)
        restore_start = dynamic_controls[1]
        restore = corrected[restore_start : restore_start + 7]
        del corrected[restore_start : restore_start + 7]

        self.assertEqual(
            tuple(record.name for record in restore),
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

        def comparable(
            records: list[KnownCommand],
        ) -> tuple[tuple[str, dict[str, object]], ...]:
            result = []
            for record in records:
                values = dict(record.values)
                if record.name == "file_checksum":
                    values["value"] = 0
                result.append((record.name, values))
            return tuple(result)

        self.assertEqual(
            comparable(corrected),
            comparable(uncorrected),
        )

    def test_observations_cover_failure_and_corrected_restore(self) -> None:
        manifest = _manifest()
        second = manifest["jobs"][1]["operator_observation"]
        third = manifest["jobs"][2]["operator_observation"]

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
            third["reported_verbatim"],
            ["Perfect. A ~30mm line, a gap, and a ~30mm line"],
        )
        self.assertEqual(
            third["explicit_baseline_restore_effect_evidence"],
            "operator-observed",
        )
        self.assertEqual(
            manifest["result"]["explicit_baseline_restore_effect_evidence"],
            "operator-observed",
        )
        self.assertEqual(
            manifest["result"]["mode_wide_execution_evidence"],
            "not-observed",
        )
        for job in manifest["jobs"]:
            receipt = job["transmission"]
            self.assertEqual(
                receipt["receipt_scope"],
                "host-side-transfer-summary",
            )
            self.assertFalse(receipt["controller_acknowledgement"])
            self.assertFalse(receipt["execution_acknowledgement"])
        profile = LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        self.assertEqual(profile.execution_evidence, "not-observed")
        self.assertIsNone(profile.execution_evidence_source)


if __name__ == "__main__":
    unittest.main()
