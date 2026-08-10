"""Offline verification of the supervised Ruida hardware capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import unittest

from ruida_re.api import RuidaCodec
from ruida_re.codec import swizzle, unswizzle
from ruida_re.job import (
    Bounds,
    JobPlan,
    LayerPlan,
    MarkTo,
    RuidaJobCompiler,
    SetModulation,
    TravelTo,
)
from ruida_re.program import KnownCommand


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT / "fixtures/hardware/ruida-644xs-usb-serial-v1"
)
MANIFEST_PATH = FIXTURE / "manifest-v1.json"
JOB_SHA256 = (
    "e819fb250403e04876fed66c512c47bc4abfcf33e19c2eaecddba2bd536aeb6d"
)
JOB_SIZE = 689


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _event(item: dict[str, Any]) -> TravelTo | MarkTo | SetModulation:
    event_type = item["type"]
    if event_type == "travel_to":
        return TravelTo(item["x_mm"], item["y_mm"])
    if event_type == "mark_to":
        return MarkTo(item["x_mm"], item["y_mm"])
    if event_type == "set_modulation":
        return SetModulation(item["percent"])
    raise ValueError(f"Unknown hardware-fixture event: {event_type}")


def _plan(manifest: dict[str, Any]) -> JobPlan:
    layers = []
    for item in manifest["job"]["layers"]:
        raster = {}
        if item["kind"] == "raster":
            raster = {
                "scan_axis": item["scan_axis"],
                "raster_strategy": item["raster_strategy"],
            }
        layers.append(
            LayerPlan(
                index=item["index"],
                speed_mm_s=item["speed_mm_s"],
                min_power_percent=item["min_power_percent"],
                max_power_percent=item["max_power_percent"],
                events=tuple(_event(event) for event in item["events"]),
                kind=item["kind"],
                air_assist=item["air_assist"],
                color_rgb=item["color_rgb"],
                laser_index=item["laser_index"],
                **raster,
            )
        )
    return JobPlan(tuple(layers))


class HardwareValidationFixtureTest(unittest.TestCase):
    def test_manifest_and_job_are_content_addressed(self) -> None:
        manifest = _manifest()
        job = manifest["job"]
        path = FIXTURE / job["file"]
        data = path.read_bytes()

        self.assertEqual(
            manifest["schema"],
            "ruida-re.hardware-validation.v1",
        )
        self.assertEqual(
            manifest["identifier"],
            "hardware-ruida-644xs-usb-serial-v1",
        )
        self.assertEqual(job["size_bytes"], JOB_SIZE)
        self.assertEqual(job["sha256"], JOB_SHA256)
        self.assertEqual(len(data), JOB_SIZE)
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            JOB_SHA256,
        )
        serialized = json.dumps(manifest).lower()
        for private_path in ("/dev/", "/users/", "cu.usb", "tty.usb"):
            self.assertNotIn(private_path, serialized)

    def test_job_is_fully_known_and_round_trips_exactly(self) -> None:
        manifest = _manifest()
        job = manifest["job"]
        data = (FIXTURE / job["file"]).read_bytes()
        codec = RuidaCodec()

        program = codec.decode(data, container="rd")

        self.assertEqual(program.issues, [])
        self.assertEqual(job["decode"]["known_records"], 107)
        self.assertEqual(len(program.records), 107)
        self.assertTrue(
            all(isinstance(record, KnownCommand) for record in program.records)
        )
        self.assertEqual(job["decode"]["opaque_records"], 0)
        self.assertEqual(job["decode"]["issues"], [])
        self.assertEqual(codec.encode(program), data)
        self.assertEqual(
            codec.encode(program, checksum_policy="recompute"),
            data,
        )

    def test_read_only_request_and_reply_are_exact(self) -> None:
        manifest = _manifest()
        exchange = manifest["read_only_exchange"]
        magic = int(
            manifest["environment"]["transport"]["magic_hex"],
            16,
        )

        request_logical = bytes.fromhex(
            exchange["request"]["logical_hex"]
        )
        request_wire = bytes.fromhex(exchange["request"]["wire_hex"])
        self.assertEqual(swizzle(request_logical, magic), request_wire)
        self.assertEqual(unswizzle(request_wire, magic), request_logical)
        request_codec = RuidaCodec(magic=magic, context="request")
        request = request_codec.decode(
            request_logical,
            container="logical",
        )
        self.assertEqual(request.issues, [])
        self.assertEqual(request_codec.encode(request), request_logical)
        self.assertEqual(len(request.records), 1)
        self.assertIsInstance(request.records[0], KnownCommand)
        self.assertEqual(request.records[0].name, "get_setting")
        self.assertEqual(request.records[0].values, {"address": 5})

        reply_logical = bytes.fromhex(
            exchange["response"]["logical_hex"]
        )
        reply_wire = bytes.fromhex(exchange["response"]["wire_hex"])
        self.assertEqual(swizzle(reply_logical, magic), reply_wire)
        self.assertEqual(unswizzle(reply_wire, magic), reply_logical)
        reply_codec = RuidaCodec(magic=magic, context="reply")
        reply = reply_codec.decode(reply_logical, container="logical")
        self.assertEqual(reply.issues, [])
        self.assertEqual(reply_codec.encode(reply), reply_logical)
        self.assertEqual(len(reply.records), 1)
        self.assertIsInstance(reply.records[0], KnownCommand)
        self.assertEqual(reply.records[0].name, "setting_reply")
        decoded = exchange["response"]["decoded"]
        self.assertEqual(decoded["command"], "setting_reply")
        self.assertEqual(
            reply.records[0].values,
            {
                "address": decoded["address"],
                "value": decoded["value"],
            },
        )

    def test_manifest_plan_recompiles_byte_exactly(self) -> None:
        manifest = _manifest()
        job = manifest["job"]
        expected = (FIXTURE / job["file"]).read_bytes()

        result = RuidaJobCompiler().compile(_plan(manifest))

        bounds = job["bounds_mm"]
        self.assertEqual(
            result.bounds,
            Bounds(
                bounds["min_x"],
                bounds["min_y"],
                bounds["max_x"],
                bounds["max_y"],
            ),
        )
        self.assertEqual(
            result.marked_distance_mm,
            job["marked_distance_mm"],
        )
        self.assertEqual(result.bounds, Bounds(20, 20, 30, 26))
        self.assertEqual(result.marked_distance_mm, 22)
        self.assertEqual(result.profile.identifier, job["compiler_profile"])
        self.assertEqual(result.encode_rd(), expected)

    def test_transmission_observations_are_bounded(self) -> None:
        manifest = _manifest()
        transmissions = manifest["transmissions"]

        self.assertEqual(
            [
                (
                    item["packet_count"],
                    item["payload_bytes"],
                    item["retries"],
                    item["job_sha256"],
                )
                for item in transmissions
            ],
            [
                (1, JOB_SIZE, 0, JOB_SHA256),
                (1, JOB_SIZE, 0, JOB_SHA256),
            ],
        )
        self.assertEqual(
            manifest["result"]["status"],
            "operator-reported-success",
        )
        self.assertFalse(
            manifest["environment"]["transport"][
                "job_acknowledgement_available"
            ]
        )


if __name__ == "__main__":
    unittest.main()
