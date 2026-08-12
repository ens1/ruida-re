"""Verify scoped Boss LS2040 C611-after-travel evidence."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from ruida_re import KnownCommand, RuidaCodec

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT / "fixtures" / "hardware" / "boss-ls2040-usb-serial-rayforge-dwell-travel-v1"
)
MANIFEST = EVIDENCE / "manifest-v1.json"


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _program(
    path: Path,
) -> tuple[bytes, Any, tuple[KnownCommand, ...]]:
    raw = path.read_bytes()
    program = RuidaCodec(context="job").decode(raw, container="rd")
    records = tuple(
        record for record in program.records if isinstance(record, KnownCommand)
    )
    return raw, program, records


def _cases() -> tuple[tuple[str, dict[str, Any], Path], ...]:
    artifacts = _manifest()["artifacts"]
    return tuple(
        (name, metadata, EVIDENCE / metadata["file"])
        for name, metadata in artifacts.items()
    )


def _relevant_sequence(
    records: tuple[KnownCommand, ...],
) -> list[tuple[str, float, float]]:
    x: float | None = None
    y: float | None = None
    sequence = []
    for record in records:
        if record.name in {"move_absolute", "cut_absolute"}:
            x = record.values["x_mm"]
            y = record.values["y_mm"]
        elif record.name != "additional_delay":
            continue
        assert x is not None
        assert y is not None
        sequence.append((record.name, x, y))
    return sequence


def _without_delays_or_checksum(
    records: tuple[KnownCommand, ...],
) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    normalized = []
    for record in records:
        if record.name in {"additional_delay", "file_checksum"}:
            continue
        normalized.append((record.name, record.opcode, record.values))
    return tuple(normalized)


def test_artifacts_are_content_addressed_and_roundtrip_exactly() -> None:
    manifest = _manifest()
    codec = RuidaCodec(context="job")

    assert manifest["schema"] == ("ruida-re.hardware-dwell-travel-observation.v1")
    assert manifest["identifier"] == ("boss-ls2040-usb-serial-rayforge-dwell-travel-v1")
    for _, metadata, path in _cases():
        raw, program, records = _program(path)
        assert len(raw) == metadata["size_bytes"]
        assert sha256(raw).hexdigest() == metadata["sha256"]
        assert program.issues == metadata["issues"] == []
        assert len(program.records) == metadata["records"]
        assert len(records) == metadata["known_records"]
        assert len(records) == len(program.records)
        assert metadata["opaque_records"] == 0
        assert program.source_checksum_basis == metadata["checksum"]
        assert codec.encode(program, container="rd") == raw
        assert (
            codec.encode(
                program,
                container="rd",
                checksum_policy="recompute",
            )
            == raw
        )

    assert {path.name for path in EVIDENCE.iterdir()} == {
        "README.md",
        "manifest-v1.json",
        *[path.name for _, _, path in _cases()],
    }


def test_stages_differ_only_by_c611_records_and_checksum() -> None:
    decoded = {name: _program(path)[2] for name, _, path in _cases()}
    baseline = _without_delays_or_checksum(decoded["control"])

    assert _without_delays_or_checksum(decoded["sentinel"]) == baseline
    assert _without_delays_or_checksum(decoded["full"]) == baseline

    for name, metadata, _ in _cases():
        records = decoded[name]
        names = [record.name for record in records]
        delays = [record for record in records if record.name == "additional_delay"]
        assert names.count("move_absolute") == metadata["travel_records"]
        assert names.count("cut_absolute") == metadata["mark_records"]
        assert names.count("laser_interval") == 0
        assert len(delays) == metadata["c611_additional_delay_records"]
        assert [record.opcode for record in delays] == ["c611"] * len(delays)
        assert [record.values["time_ms"] for record in delays] == (
            metadata["c611_additional_delay_ms"]
        )


def test_c611_records_immediately_follow_post_anchor_travel() -> None:
    decoded = {name: _program(path)[2] for name, _, path in _cases()}
    control = [
        ("move_absolute", 110.0, 132.0),
        ("cut_absolute", 115.0, 132.0),
        ("move_absolute", 125.0, 132.0),
        ("move_absolute", 145.0, 132.0),
        ("move_absolute", 145.0, 147.0),
        ("move_absolute", 125.0, 147.0),
    ]
    sentinel = control[:3] + [("additional_delay", 125.0, 132.0)] + control[3:]
    full = control[:2]
    for item in control[2:]:
        full.extend([item, ("additional_delay", item[1], item[2])])

    assert _relevant_sequence(decoded["control"]) == control
    assert _relevant_sequence(decoded["sentinel"]) == sentinel
    assert _relevant_sequence(decoded["full"]) == full

    artifacts = _manifest()["artifacts"]
    assert artifacts["sentinel"]["c611_after_travel_points_mm"] == [[125.0, 132.0]]
    assert artifacts["full"]["c611_after_travel_points_mm"] == [
        [125.0, 132.0],
        [145.0, 132.0],
        [145.0, 147.0],
        [125.0, 147.0],
    ]


def test_anchor_is_the_only_mark_and_uses_the_scoped_settings() -> None:
    manifest = _manifest()
    process = manifest["process"]
    for _, _, path in _cases():
        _, _, records = _program(path)
        cuts = [record for record in records if record.name.startswith("cut_")]
        assert [(record.name, record.values) for record in cuts] == [
            ("cut_absolute", {"x_mm": 115.0, "y_mm": 132.0})
        ]
        assert [
            record.values
            for record in records
            if record.name == "enable_laser_tube_start"
        ] == [{"enabled": 1}]
        assert [
            record.values["speed_mm_s"]
            for record in records
            if record.name == "active_speed"
        ] == [process["speed_mm_s"]]
        assert [
            record.values["power_percent"]
            for record in records
            if record.name
            in {
                "layer_laser_1_min_power",
                "layer_laser_1_max_power",
                "laser_1_min_power",
                "laser_1_max_power",
            }
        ] == [process["encoded_anchor_power_percent"]] * 4

    assert process["anchor"] == {
        "start_mm": [110.0, 132.0],
        "end_mm": [115.0, 132.0],
        "planned_length_mm": 5.0,
        "mark_records": 1,
    }
    assert process["controller_bounds_mm"] == {
        "min_x": 110.0,
        "min_y": 132.0,
        "max_x": 145.0,
        "max_y": 147.0,
    }


def test_observations_and_transport_claims_remain_scoped() -> None:
    manifest = _manifest()
    observations = manifest["operator_observations"]
    result = manifest["result"]

    assert observations["control"]["reported_verbatim"] == (
        "I see one faint line, vertical, about 5mm"
    )
    assert observations["sentinel"]["reported_verbatim"] == (
        "It looks like it did a rectangle with pauses at the corner? "
        "Nothing other than a horizontal line, about 5mm"
    )
    assert observations["full"]["reported_verbatim"] == (
        "Yes, one faint line, pauses at the corners"
    )
    assert not any(
        observation["instrumented_metrology"] for observation in observations.values()
    )

    for name, metadata, _ in _cases():
        transmission = manifest["transmission"][name]
        assert transmission["explicit_operator_approval"] is True
        assert transmission["artifact_sha256"] == metadata["sha256"]
        assert transmission["host_log"] == {
            "scope": "host-side driver transfer summary",
            "packets": 1,
            "payload_bytes": metadata["size_bytes"],
            "retries": 0,
            "controller_acknowledgement": False,
            "execution_acknowledgement": False,
        }

    assert result == {
        "status": "scoped-c611-after-travel-pass",
        "one_c611_after_travel": "qualitative-operator-observation-recorded",
        "four_c611_after_travel": "operator-confirmed-pauses-at-corners",
        "c611_delay_duration_metrology": "not-performed",
        "post_anchor_visible_marking": "not-observed-in-all-three-stages",
        "mark_adjacent_c611": "not-tested",
        "other_c611_durations": "not-tested",
        "c610_pulse_execution": "not-tested",
        "broad_profile_conclusion": "not-established",
    }
    relationship = manifest["relationship_to_prior_zero_power_evidence"]
    assert relationship["quarantined_200ms_artifact_superseded"] is False
    assert relationship["disposition"] == "remains-quarantined-do-not-send"

    serialized = json.dumps(manifest).lower()
    for private_path in (
        "/dev/",
        "/tmp/",
        "/private/",
        "/users/",
        "cu.usb",
        "tty.usb",
        "usbserial",
        "usbmodem",
    ):
        assert private_path not in serialized
