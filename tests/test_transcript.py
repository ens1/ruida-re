"""Tests for versioned, lossless multi-datagram transcripts."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

from ruida_re.transcript import (
    SCHEMA,
    Datagram,
    Endpoint,
    Transcript,
    capture_datagram,
)
from ruida_re.transport import encode_datagram


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas/transcript-v1.schema.json"
PROGRAM_SCHEMA_PATH = ROOT / "schemas/program-v1.schema.json"


class TranscriptTest(unittest.TestCase):
    def test_endpoint_normalizes_integer_valued_json_port(self) -> None:
        endpoint = Endpoint.from_dict(
            {"address": "192.0.2.10", "port": 50200.0}
        )
        self.assertEqual(endpoint.port, 50200)
        self.assertIsInstance(endpoint.port, int)
        with self.assertRaises(ValueError):
            Endpoint.from_dict(
                {"address": "192.0.2.10", "port": 50200.5}
            )

    def test_outbound_request_uses_checksum_framing(self) -> None:
        logical = bytes.fromhex("da000001")
        raw = encode_datagram(logical, context="request")
        datagram = capture_datagram(raw, "outbound", "request")
        self.assertIsNotNone(datagram.program)
        self.assertEqual(datagram.raw_bytes(), raw)
        self.assertEqual(datagram.program.container, "udp")
        self.assertEqual(datagram.program.context, "request")
        self.assertEqual(datagram.program.encode(), raw)

    def test_inbound_reply_has_no_checksum_prefix(self) -> None:
        raw = encode_datagram(b"\xcc", context="reply")
        self.assertEqual(raw, b"\xc6")
        datagram = capture_datagram(raw, "inbound", "reply")
        self.assertIsNotNone(datagram.program)
        self.assertEqual(datagram.program.records[0].name, "acknowledge")
        self.assertEqual(datagram.program.encode(), raw)

    def test_json_round_trip_preserves_order_and_boundaries(self) -> None:
        controller = Endpoint("192.0.2.10", 50200)
        host = Endpoint("192.0.2.20", 40200)
        request = encode_datagram(
            bytes.fromhex("da000001"),
            context="request",
        )
        reply = encode_datagram(b"\xcc", context="reply")
        job = encode_datagram(b"\xd7", context="job")
        transcript = Transcript()
        transcript.capture(
            request,
            "outbound",
            "request",
            timestamp=1_723_000_000.125,
            source=host,
            destination=controller,
        )
        transcript.capture(
            reply,
            "inbound",
            "reply",
            timestamp=1_723_000_000.25,
            source=controller,
            destination=host,
        )
        transcript.capture(job, "outbound", "job")
        restored = Transcript.from_json(transcript.to_json())
        self.assertEqual(restored.to_dict(), transcript.to_dict())
        self.assertEqual(
            restored.raw_datagrams(),
            (request, reply, job),
        )
        self.assertEqual(restored.datagrams[0].source, host)
        self.assertEqual(restored.datagrams[1].source, controller)
        self.assertIsNone(restored.datagrams[2].timestamp)

    def test_timestamp_numbers_are_exact_and_resource_bounded(self) -> None:
        raw = encode_datagram(b"\xcc", context="reply")
        datagram = capture_datagram(
            raw,
            "inbound",
            "reply",
            timestamp=0.1,
        )
        document = Transcript([datagram]).to_json(indent=None)
        restored = Transcript.from_json(document)
        self.assertEqual(restored.datagrams[0].timestamp, 0.1)

        canonicalized = document.replace(
            '"timestamp": 0.1',
            '"timestamp": 0.10000000000000001',
            1,
        )
        parsed = Transcript.from_json(canonicalized)
        self.assertEqual(parsed.datagrams[0].timestamp, 0.1)

        for value in ("1e309", "1e1000000000"):
            with self.subTest(value=value):
                invalid = document.replace(
                    '"timestamp": 0.1',
                    f'"timestamp": {value}',
                    1,
                )
                self.assertNotEqual(invalid, document)
                with self.assertRaisesRegex(
                    ValueError,
                    "interoperable numeric",
                ):
                    Transcript.from_json(invalid)

        with self.assertRaises(ValueError):
            capture_datagram(
                raw,
                "inbound",
                "reply",
                timestamp=10**10000,
            )

    def test_invalid_outbound_checksum_remains_captured(self) -> None:
        raw = bytearray(
            encode_datagram(bytes.fromhex("da000001"), context="request")
        )
        raw[-1] ^= 1
        datagram = capture_datagram(bytes(raw), "outbound", "request")
        self.assertIsNone(datagram.program)
        self.assertEqual(datagram.raw_bytes(), bytes(raw))
        self.assertIn("ChecksumError", datagram.issues[0])
        restored = Transcript.from_json(Transcript([datagram]).to_json())
        self.assertEqual(restored.raw_datagrams(), (bytes(raw),))
        self.assertIsNone(restored.datagrams[0].program)

    def test_program_issues_are_exposed_on_the_datagram(self) -> None:
        raw = encode_datagram(b"\x01", context="request")
        datagram = capture_datagram(raw, "outbound", "request")
        self.assertIsNotNone(datagram.program)
        self.assertEqual(datagram.issues, datagram.program.issues)
        self.assertTrue(datagram.issues)

    def test_flow_decode_reassembles_commands_split_across_packets(
        self,
    ) -> None:
        first = encode_datagram(bytes.fromhex("da00"), context="request")
        second = encode_datagram(bytes.fromhex("0001"), context="request")
        transcript = Transcript()
        transcript.capture(first, "outbound", "request")
        transcript.capture(
            encode_datagram(b"\xcc", context="reply"),
            "inbound",
            "reply",
        )
        transcript.capture(second, "outbound", "request")
        program = transcript.decode_flow("outbound", "request")
        self.assertEqual(len(program.records), 1)
        self.assertEqual(program.records[0].name, "get_setting")
        self.assertEqual(program.records[0].values, {"address": 1})

    def test_flow_decode_requires_a_matching_datagram(self) -> None:
        with self.assertRaisesRegex(ValueError, "no outbound job"):
            Transcript().decode_flow("outbound", "job")

    def test_direction_and_context_must_agree(self) -> None:
        with self.assertRaises(ValueError):
            capture_datagram(b"\xc6", "outbound", "reply")
        with self.assertRaises(ValueError):
            capture_datagram(b"\xc6", "inbound", "request")

    def test_json_program_must_reproduce_captured_bytes(self) -> None:
        raw = encode_datagram(
            bytes.fromhex("da000001"),
            context="request",
        )
        datagram = capture_datagram(raw, "outbound", "request")
        data = Transcript([datagram]).to_dict()
        data["datagrams"][0]["raw"] = "0000"
        with self.assertRaises(ValueError):
            Transcript.from_dict(data)

    def test_mutated_program_cannot_silently_replace_capture(self) -> None:
        raw = encode_datagram(
            bytes.fromhex("da000001"),
            context="request",
        )
        datagram = capture_datagram(raw, "outbound", "request")
        datagram.program.records[0].values["address"] = 2
        with self.assertRaises(ValueError):
            datagram.to_dict()

    def test_mutated_metadata_cannot_emit_invalid_json(self) -> None:
        raw = encode_datagram(b"\xcc", context="reply")
        cases = (
            ("direction", "sideways"),
            ("issues", [123]),
            ("timestamp", float("inf")),
            ("raw", "C6"),
        )
        for name, value in cases:
            with self.subTest(name=name):
                datagram = capture_datagram(raw, "inbound", "reply")
                setattr(datagram, name, value)
                with self.assertRaises(ValueError):
                    datagram.to_dict()

    def test_schema_is_versioned_and_matches_runtime(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        program_schema = json.loads(
            PROGRAM_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema"]["const"], SCHEMA)
        self.assertEqual(schema["$id"], "urn:ruida-re:schema:transcript:v1")
        program_ref = schema["$defs"]["udpProgram"]["allOf"][0]["$ref"]
        self.assertEqual(program_ref, "#/$defs/program")
        self.assertEqual(schema["$defs"]["program"], program_schema)
        raw = encode_datagram(b"\xcc", context="reply")
        transcript = Transcript(
            [capture_datagram(raw, "inbound", "reply")]
        )
        Draft202012Validator(schema).validate(transcript.to_dict())
        request = encode_datagram(
            bytes.fromhex("da000005"),
            context="request",
        )
        hardware = Transcript(
            [capture_datagram(request, "outbound", "request")]
        )
        record = hardware.datagrams[0].program.records[0]
        self.assertEqual(record.shape_evidence, "hardware-observed")
        self.assertEqual(record.semantic_evidence, "hardware-observed")
        Draft202012Validator(schema).validate(hardware.to_dict())
        mismatched = transcript.to_dict()
        mismatched["datagrams"][0]["direction"] = "outbound"
        mismatched["datagrams"][0]["context"] = "job"
        mismatched["datagrams"][0]["program"]["context"] = "request"
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(mismatched)
        invalid_program = transcript.datagrams[0].program.to_dict()
        invalid_program["container"] = "logical"
        invalid_program["header"] = "00"
        with self.assertRaises(ValidationError):
            Draft202012Validator(program_schema).validate(invalid_program)
        invalid_program["container"] = "rd"
        with self.assertRaises(ValidationError):
            Draft202012Validator(program_schema).validate(invalid_program)

    def test_from_dict_rejects_issue_string(self) -> None:
        with self.assertRaises(ValueError):
            Datagram.from_dict(
                {
                    "direction": "outbound",
                    "context": "request",
                    "raw": "00",
                    "program": None,
                    "issues": "invalid",
                }
            )

    def test_json_rejects_duplicate_keys(self) -> None:
        value = (
            '{"schema":"ruida-re.transcript.v1",'
            '"datagrams":[],"datagrams":[]}'
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            Transcript.from_json(value)

    def test_json_rejects_unknown_fields_at_every_level(self) -> None:
        raw = encode_datagram(b"\xcc", context="reply")
        value = Transcript(
            [capture_datagram(raw, "inbound", "reply")]
        ).to_dict()
        cases = (
            (value, "extra"),
            (value["datagrams"][0], "extra"),
        )
        for target, key in cases:
            with self.subTest(target=target):
                target[key] = True
                with self.assertRaisesRegex(ValueError, "extra"):
                    Transcript.from_dict(value)
                del target[key]
        value["datagrams"][0]["source"] = {
            "address": "192.0.2.20",
            "port": 40200,
            "extra": True,
        }
        with self.assertRaisesRegex(ValueError, "extra"):
            Transcript.from_dict(value)

    def test_json_rejects_noncanonical_raw_hex(self) -> None:
        raw = encode_datagram(b"\xcc", context="reply")
        value = Transcript(
            [capture_datagram(raw, "inbound", "reply")]
        ).to_dict()
        for invalid in ("C6", "c 6", "c6 "):
            with self.subTest(raw=invalid):
                value["datagrams"][0]["raw"] = invalid
                with self.assertRaisesRegex(ValueError, "canonical"):
                    Transcript.from_dict(value)


if __name__ == "__main__":
    unittest.main()
