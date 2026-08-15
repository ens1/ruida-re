"""Offline verification of the address-512 zero-value capture."""

from __future__ import annotations

import json
from pathlib import Path

from ruida_re import RuidaCodec, swizzle, unswizzle
from ruida_re.program import KnownCommand


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT
    / "fixtures/hardware/boss-ls2040-usb-serial-address-512-zero-v1"
    / "manifest-v1.json"
)


def test_address_512_capture_is_exact_and_narrowly_classified() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    operation = manifest["operation"]
    request = operation["request"]
    response = operation["response"]
    magic = manifest["environment"]["transport"]["magic"]
    request_logical = bytes.fromhex(request["logical_hex"])
    request_wire = bytes.fromhex(request["wire_hex"])
    response_logical = bytes.fromhex(response["logical_hex"])
    response_wire = bytes.fromhex(response["wire_hex"])

    assert request["semantic_address"] == 0x0200
    assert request["encoded_address_groups_hex"] == "0400"
    assert request_logical == bytes.fromhex("da000400")
    assert request_wire == bytes.fromhex("d4898d89")
    assert response_logical == bytes.fromhex("da0104000000000000")
    assert response_wire == bytes.fromhex("d4098d898989898989")
    assert swizzle(request_logical, magic) == request_wire
    assert unswizzle(response_wire, magic) == response_logical

    request_program = RuidaCodec(
        magic=magic,
        context="request",
    ).decode(request_logical, container="logical")
    reply_program = RuidaCodec(
        magic=magic,
        context="reply",
    ).decode(response_logical, container="logical")
    assert request_program.issues == []
    assert reply_program.issues == []
    request_record = request_program.records[0]
    reply_record = reply_program.records[0]
    assert isinstance(request_record, KnownCommand)
    assert isinstance(reply_record, KnownCommand)
    assert request_record.values == {"address": 0x0200}
    assert reply_record.values == {
        "address": 0x0200,
        "value": 0,
    }
    assert operation["receipt"] == {
        "packets": 1,
        "transmissions": 1,
        "retries": 0,
        "completed_packets": 1,
    }
    assert manifest["evidence"]["zero_means_idle"] == "not-established"
    assert (
        manifest["evidence"]["active_or_completion_transition"]
        == "not-observed"
    )
    assert (
        manifest["evidence"]["physical_effect"]
        == "not-assessed-no-operator-report"
    )


def test_address_512_capture_contains_no_private_device_path() -> None:
    serialized = MANIFEST_PATH.read_text(encoding="utf-8").lower()
    for private_path in ("/dev/", "/users/", "cu.usb", "tty.usb"):
        assert private_path not in serialized
