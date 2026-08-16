"""Offline verification of scoped Focus Distance write evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ruida_re import RuidaCodec, swizzle
from ruida_re.program import KnownCommand

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "fixtures/hardware"
    / "operator-controlled-ruida-usb-serial-focus-distance-write-v1"
)
MANIFEST = FIXTURE / "manifest-v1.json"
README = FIXTURE / "README.md"


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_focus_distance_write_packets_are_exact_da01_records() -> None:
    manifest = _manifest()
    protocol = manifest["protocol"]
    codec = RuidaCodec(
        magic=manifest["environment"]["transport"]["magic"],
        context="request",
    )

    assert manifest["schema"] == (
        "ruida-re.hardware-focus-distance-write-observation.v1"
    )
    assert manifest["identifier"] == (
        "operator-controlled-ruida-usb-serial-focus-distance-write-v1"
    )
    assert manifest["observed_on"] == "2026-08-16"
    assert manifest["runtime_revisions"]["ruida_re"] == (
        "ed64c7449b9c83ca2d168eb86ed391562e91fcc3"
    )
    assert protocol["semantic_address"] == 0x010E
    assert protocol["read_request"]["logical_hex"] == "da00020e"
    read = codec.decode(
        bytes.fromhex(protocol["read_request"]["logical_hex"]),
        container="logical",
    )
    assert read.issues == []
    assert len(read.records) == 1
    read_record = read.records[0]
    assert isinstance(read_record, KnownCommand)
    assert read_record.name == "get_setting"
    assert read_record.values == {"address": 0x010E}

    for key, expected_raw, requested_raw in (
        ("forward_write", 9300, 9400),
        ("rollback_write", 9400, 9300),
    ):
        item = protocol[key]
        logical = bytes.fromhex(item["logical_hex"])
        wire = bytes.fromhex(item["serial_wire_hex"])
        decoded = codec.decode(logical, container="logical")

        assert decoded.issues == []
        assert len(decoded.records) == 1
        record = decoded.records[0]
        assert item["expected_raw"] == expected_raw
        assert item["requested_raw"] == requested_raw
        assert isinstance(record, KnownCommand)
        assert record.name == "set_setting"
        assert record.values == {
            "address": 0x010E,
            "first_value": requested_raw,
            "second_value": requested_raw,
        }
        assert swizzle(logical, codec.magic) == wire


def test_host_write_and_fresh_readback_observations_are_scoped() -> None:
    manifest = _manifest()
    observations = manifest["host_observations"]

    assert observations["baseline"]["da00_raw_values"] == [9300] * 3
    assert observations["baseline"]["session_closed_after_samples"] is True
    for key, prior, requested, readback in (
        ("forward", 9300, 9400, 9400),
        ("rollback", 9400, 9300, 9300),
    ):
        observation = observations[key]
        receipt = observation["send_receipt"]
        protocol = manifest["protocol"][f"{key}_write"]

        assert observation["calls"] == 1
        assert observation["cas_prior_raw"] == prior
        assert observation["requested_raw"] == requested
        assert observation["da01_write_attempts"] == 1
        assert receipt == {
            "packet_count": 1,
            "packet_hex": protocol["serial_wire_hex"],
            "transmissions": 1,
            "retries": 0,
            "completed_packets": 1,
        }
        assert observation["method_readback_performed"] is False
        fresh = observation["fresh_connection_readback"]
        assert fresh["separate_client_session"] is True
        assert fresh["write_session_closed_before_readback_connection_opened"] is True
        assert fresh["da00_raw_values"] == [readback] * 3


def test_operator_attestations_and_nonclaims_remain_distinct() -> None:
    manifest = _manifest()
    attestations = manifest["operator_attestations"]
    result = manifest["result"]

    assert attestations["pre_write_conditions"]["reported_verbatim"] == ("All are true")
    assert attestations["forward_setting_display"] == {
        "focus_distance_display_mm": 9.4,
        "reported_verbatim": "Yes, focus distance is 9.4 now",
    }
    assert attestations["forward_panel_autofocus"] == {
        "completed": True,
        "final_z_display_mm": 9.4,
        "reported_verbatim": "Yes, it reads 9.4",
    }
    assert attestations["rollback_panel_autofocus"]["completed"] is True
    assert attestations["rollback_panel_autofocus"]["final_z_display_mm"] == 9.3
    assert attestations["rollback_panel_autofocus"]["reported_verbatim"] == "It did"
    assert result["power_cycle_persistence"] == "not-tested"
    assert result["contact_probe_trigger_or_coordinate"] == (
        "not-observed-or-validated"
    )
    assert result["live_d82e_autofocus"] == "not-tested"
    assert result["other_controllers_or_values"] == "not-established"


def test_source_capture_preconditions_and_limitations_are_explicit() -> None:
    manifest = _manifest()
    source_capture = manifest["source_capture"]
    preconditions = manifest["environment"]["physical_preconditions"]
    limitations = " ".join(manifest["scope"]["limitations"])

    assert manifest["scope"]["controller_identity"] == (
        "exact operator-controlled controller; model and firmware not "
        "independently captured"
    )
    assert source_capture["standalone_raw_transcript"] == {
        "available": False,
        "sha256": None,
        "reason": (
            "The supervised session retained parsed DA00 readings and "
            "FocusDistanceWriteReceipt and SendReceipt values, but no "
            "standalone raw transport transcript file was saved."
        ),
    }
    assert source_capture["published_evidence_basis"] == [
        "Parsed DA00 raw values recorded during the supervised session.",
        (
            "FocusDistanceWriteReceipt and SendReceipt values recorded "
            "during the supervised session."
        ),
        "Operator attestations retained from the supervised conversation.",
    ]
    assert preconditions == {
        "source_or_high_voltage_off": "operator-attested",
        "controller_idle": "operator-attested",
        "panel_exclusion_during_host_exchange": "operator-attested",
        "physical_supervision": "operator-present",
    }
    for required_nonclaim in (
        "exact controller, transport, runtime revision, values",
        "not a controller acknowledgement",
        "separately opened fresh connection",
        "reset or power cycle",
        "operator attestations, not host telemetry",
        "No probe-trigger signal, contact event, contact coordinate",
        "logical D82E was not transmitted",
        "do not validate autofocus safety, repeatability",
        "do not establish a generic unit conversion",
        "Private host paths and device identifiers",
    ):
        assert required_nonclaim in limitations


def test_published_fixture_contains_no_private_device_identifier() -> None:
    serialized = (
        MANIFEST.read_text(encoding="utf-8") + README.read_text(encoding="utf-8")
    ).lower()
    for private_value in (
        "/dev/",
        "/private/",
        "/users/",
        "cu.usb",
        "tty.usb",
        "usbserial",
        "usbmodem",
    ):
        assert private_value not in serialized
    assert "power cycle" in serialized
    assert "contact" in serialized
