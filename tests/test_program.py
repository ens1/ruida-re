"""Tests for lossless file-to-JSON translation."""

from __future__ import annotations

from copy import deepcopy
import json
import random
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from ruida_re.codec import swizzle, unswizzle
from ruida_re.program import KnownCommand, Program, RawSpan, decode
from ruida_re.transport import encode_datagram


ROOT = Path(__file__).resolve().parents[1]
RD_PATH = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"
SCHEMA_PATH = ROOT / "schemas/program-v1.schema.json"


class ProgramTest(unittest.TestCase):
    def test_baseline_is_fully_segmented_and_lossless(self) -> None:
        raw_data = RD_PATH.read_bytes()
        program = decode(raw_data)
        self.assertEqual(program.issues, [])
        self.assertEqual(len(program.records), 70)
        self.assertTrue(
            all(isinstance(record, KnownCommand) for record in program.records)
        )
        self.assertEqual(program.encode(), raw_data)

    def test_json_translation_is_lossless(self) -> None:
        raw_data = RD_PATH.read_bytes()
        document = decode(raw_data).to_json()
        self.assertEqual(Program.from_json(document).encode(), raw_data)

    def test_integer_valued_json_numbers_are_normalized(self) -> None:
        raw_data = swizzle(bytes.fromhex("ca0201"))
        document = decode(raw_data).to_dict()
        document["magic"] = 136.0
        document["source_checksum_basis"] = float(
            document["source_checksum_basis"]
        )
        record = document["records"][0]
        record["offset"] = 0.0
        record["values"]["layer"] = 1.0
        restored = Program.from_json(json.dumps(document))
        self.assertEqual(restored.magic, 136)
        self.assertIsInstance(restored.magic, int)
        self.assertIsInstance(restored.source_checksum_basis, int)
        self.assertEqual(restored.records[0].offset, 0)
        self.assertEqual(restored.records[0].values["layer"], 1)
        self.assertEqual(restored.encode(), raw_data)

        raw_document = Program(
            records=[RawSpan(offset=0, raw="01")]
        ).to_dict()
        raw_document["records"][0]["offset"] = 0.0
        raw_program = Program.from_dict(raw_document)
        self.assertEqual(raw_program.records[0].offset, 0)

    def test_fractional_json_integers_remain_invalid(self) -> None:
        document = decode(swizzle(bytes.fromhex("ca0201"))).to_dict()
        for field, value in (
            ("magic", 136.5),
            ("source_checksum_basis", 1.5),
        ):
            with self.subTest(field=field):
                invalid = deepcopy(document)
                invalid[field] = value
                with self.assertRaises(ValueError):
                    Program.from_dict(invalid)
        invalid = deepcopy(document)
        invalid["records"][0]["offset"] = 0.5
        with self.assertRaises(ValueError):
            Program.from_dict(invalid)

    def test_json_numbers_are_exact_and_resource_bounded(self) -> None:
        raw_data = swizzle(bytes.fromhex("ca0201"))
        document = decode(raw_data).to_json(indent=None)
        precise_fraction = document.replace(
            '"layer": 1',
            '"layer": 1.0000000000000001',
            1,
        )
        self.assertNotEqual(precise_fraction, document)
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            Program.from_json(precise_fraction)

        numeric = decode(RD_PATH.read_bytes()).to_json(indent=None)
        canonicalized = numeric.replace(
            '"speed_mm_s": 10.0',
            '"speed_mm_s": 10.000000000000001',
            1,
        )
        self.assertNotEqual(canonicalized, numeric)
        restored = Program.from_json(canonicalized)
        speed = next(
            record.values["speed_mm_s"]
            for record in restored.records
            if isinstance(record, KnownCommand)
            and record.name == "layer_speed"
        )
        self.assertEqual(speed, 10.000000000000002)

        rounded_offset = document.replace(
            '"offset": 0',
            '"offset": 9.007199254740993e15',
            1,
        )
        self.assertNotEqual(rounded_offset, document)
        with self.assertRaisesRegex(ValueError, "interoperable numeric"):
            Program.from_json(rounded_offset)

        source_basis = decode(raw_data).source_checksum_basis
        amplified = document.replace(
            f'"source_checksum_basis": {source_basis}',
            '"source_checksum_basis": 1e1000000000',
            1,
        )
        self.assertNotEqual(amplified, document)
        with self.assertRaisesRegex(ValueError, "interoperable numeric"):
            Program.from_json(amplified)

        direct = decode(raw_data).to_dict()
        direct["source_checksum_basis"] = 10**10000
        with self.assertRaises(ValueError):
            Program.from_dict(direct)

    def test_json_rejects_duplicate_keys_and_nonfinite_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            Program.from_json('{"schema":"a","schema":"b"}')
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            Program.from_json('{"schema": NaN}')

    def test_json_requires_the_complete_versioned_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "fields do not match"):
            Program.from_dict({"schema": "ruida-re.program.v1"})

    def test_unknown_newer_command_with_raw_bytes_is_lossless(self) -> None:
        program = decode(bytes.fromhex("d7"), container="logical")
        data = program.to_dict()
        command = data["records"][0]
        command["name"] = "future_controller_command"
        command["values"] = {"reported_value": 1}
        restored = Program.from_dict(data)
        self.assertEqual(restored.encode(), bytes.fromhex("d7"))
        self.assertEqual(restored.to_dict(), data)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(restored.to_dict())
        restored.records[0].values["reported_value"] = 2
        with self.assertRaisesRegex(ValueError, "Cannot edit unknown"):
            restored.to_dict()
        command["values"] = {"bad": [1]}
        with self.assertRaisesRegex(ValueError, "JSON numbers or strings"):
            Program.from_dict(data)
        command["values"] = {}
        command.pop("raw")
        with self.assertRaisesRegex(ValueError, "without raw bytes"):
            Program.from_dict(data)

    def test_program_rejects_noncanonical_hex_and_boolean_numbers(
        self,
    ) -> None:
        program = Program(
            magic=True,
            container="logical",
            records=[],
        )
        with self.assertRaisesRegex(ValueError, "Magic"):
            program.encode()
        program.magic = 0x88
        program.records = [RawSpan(offset=0, raw="AA")]
        with self.assertRaisesRegex(ValueError, "lowercase"):
            program.encode()

    def test_source_raw_cannot_bypass_structured_type_validation(self) -> None:
        command = KnownCommand(
            offset=0,
            opcode="ca02",
            name="select_layer",
            values={"layer": True},
            raw="ca0201",
        )
        program = Program(container="logical", records=[command])
        with self.assertRaises(ValueError):
            program.encode()

    def test_exact_mode_preserves_an_existing_bad_job_checksum(self) -> None:
        logical = bytearray(unswizzle(RD_PATH.read_bytes()))
        logical[486:491] = b"\x00\x00\x00\x00\x01"
        raw_data = swizzle(bytes(logical))
        self.assertEqual(decode(raw_data).encode(), raw_data)

    def test_changed_value_is_reencoded(self) -> None:
        program = decode(RD_PATH.read_bytes())
        old_checksum = next(
            record.values["value"]
            for record in program.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        speed = next(
            record
            for record in program.records
            if isinstance(record, KnownCommand)
            and record.name == "layer_speed"
        )
        speed.values["speed_mm_s"] = 12.5
        changed = decode(program.encode(checksum_policy="recompute"))
        changed_speed = next(
            record
            for record in changed.records
            if isinstance(record, KnownCommand)
            and record.name == "layer_speed"
        )
        self.assertEqual(changed_speed.values["speed_mm_s"], 12.5)
        new_checksum = next(
            record.values["value"]
            for record in changed.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        self.assertNotEqual(new_checksum, old_checksum)

    def test_changed_stream_with_multiple_checksums_is_rejected(self) -> None:
        program = decode(RD_PATH.read_bytes())
        checksum = next(
            record
            for record in program.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        program.records.insert(-1, deepcopy(checksum))
        speed = next(
            record
            for record in program.records
            if isinstance(record, KnownCommand)
            and record.name == "layer_speed"
        )
        speed.values["speed_mm_s"] = 12.5
        with self.assertRaises(ValueError):
            program.encode(checksum_policy="recompute")

    def test_checksum_policy_does_not_trust_json_metadata(self) -> None:
        program = decode(RD_PATH.read_bytes())
        checksum = next(
            record
            for record in program.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        checksum.values["value"] = 1
        program.source_checksum_basis = 1
        preserved = decode(program.encode())
        preserved_checksum = next(
            record
            for record in preserved.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        self.assertEqual(preserved_checksum.values["value"], 1)
        repaired = decode(program.encode(checksum_policy="recompute"))
        repaired_checksum = next(
            record
            for record in repaired.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        self.assertEqual(
            repaired_checksum.values["value"],
            repaired.source_checksum_basis,
        )

    def test_unknown_bytes_are_retained(self) -> None:
        stream = b"\x01\x02\xd7"
        raw_data = swizzle(stream)
        program = decode(raw_data)
        self.assertIsInstance(program.records[0], RawSpan)
        self.assertEqual(program.records[0].raw, "0102")
        self.assertEqual(program.encode(), raw_data)

    def test_noncanonical_packed_name_is_lossless(self) -> None:
        logical = bytes.fromhex(
            "f202 7f7f7f7f7f 7f7f7f7f7f d7"
        )
        raw_data = swizzle(logical)
        program = decode(raw_data)
        self.assertEqual(len(program.issues), 1)
        self.assertIsInstance(program.records[0], RawSpan)
        self.assertEqual(program.encode(), raw_data)

    def test_semantic_shape_cannot_cross_a_frame_boundary(self) -> None:
        logical = bytes.fromhex("ca02 d7 e601")
        program = decode(swizzle(logical))
        self.assertIsInstance(program.records[0], RawSpan)
        self.assertEqual(program.records[0].raw, "ca02")
        self.assertEqual(program.records[1].name, "end_of_file")
        self.assertEqual(program.records[2].name, "set_absolute")
        self.assertEqual(program.encode(), swizzle(logical))

    def test_arbitrary_inputs_are_lossless(self) -> None:
        randomizer = random.Random(0x52554944)
        for context in ("job", "request", "reply"):
            for case in range(1000):
                with self.subTest(context=context, case=case):
                    size = randomizer.randrange(96)
                    raw_data = randomizer.randbytes(size)
                    self.assertEqual(
                        decode(raw_data, context=context).encode(),
                        raw_data,
                    )

    def test_rdwork_wrapper_is_retained(self) -> None:
        header = b"RDWORKV123"
        raw_data = header + swizzle(b"\xd7")
        program = decode(raw_data)
        self.assertEqual(program.header, header.hex())
        self.assertEqual(program.encode(), raw_data)

    def test_rd_container_rejects_non_wrapper_headers(self) -> None:
        for header in ("00", b"NOTWORK123".hex(), b"RDWORKV12".hex()):
            with self.subTest(header=header):
                program = Program(container="rd", header=header)
                with self.assertRaises(ValueError):
                    program.encode()

    def test_udp_container_round_trips_packet_and_json(self) -> None:
        packet = encode_datagram(bytes.fromhex("cc"), context="reply")
        program = decode(packet, context="reply", container="udp")
        self.assertEqual(program.container, "udp")
        self.assertEqual(program.records[0].name, "acknowledge")
        self.assertEqual(program.encode(), packet)
        self.assertEqual(Program.from_json(program.to_json()).encode(), packet)

    def test_udp_request_container_includes_packet_checksum(self) -> None:
        logical = bytes.fromhex("da000001")
        packet = encode_datagram(logical, context="request")
        program = decode(packet, context="request", container="udp")
        self.assertEqual(program.records[0].name, "get_setting")
        self.assertEqual(program.encode(), packet)

    def test_logical_container_does_not_scramble(self) -> None:
        logical = bytes.fromhex("d7")
        program = decode(logical, container="logical")
        self.assertEqual(program.container, "logical")
        self.assertEqual(program.records[0].name, "end_of_file")
        self.assertEqual(program.encode(), logical)

    def test_structured_command_can_be_created_without_source_bytes(
        self,
    ) -> None:
        program = Program(
            container="logical",
            records=[
                KnownCommand(
                    offset=0,
                    opcode="88",
                    name="move_absolute",
                    values={"x_mm": -1.0, "y_mm": 2.0},
                )
            ],
        )
        encoded = program.encode()
        decoded = decode(encoded, container="logical")
        self.assertEqual(
            decoded.records[0].values,
            {"x_mm": -1.0, "y_mm": 2.0},
        )

    def test_raw_wire_ack_translates_in_reply_context(self) -> None:
        program = decode(b"\xc6", context="reply", container="udp")
        self.assertEqual(program.context, "reply")
        self.assertEqual(program.records[0].name, "acknowledge")
        self.assertEqual(program.records[0].raw, "cc")
        self.assertEqual(program.encode(), b"\xc6")

    def test_setting_reply_uses_direction_specific_shape(self) -> None:
        logical = b"\xda\x01\x00\x05\x00\x00\x00\x00\x2a"
        raw_data = swizzle(logical)
        program = decode(raw_data, context="reply")
        self.assertEqual(program.records[0].name, "setting_reply")
        self.assertEqual(
            program.records[0].values,
            {"address": 5, "value": 42},
        )
        self.assertEqual(program.encode(), raw_data)


if __name__ == "__main__":
    unittest.main()
