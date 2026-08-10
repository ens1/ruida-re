"""Tests for lossless file-to-JSON translation."""

from __future__ import annotations

from copy import deepcopy
import random
import unittest
from pathlib import Path

from ruida_re.codec import swizzle, unswizzle
from ruida_re.program import KnownCommand, Program, RawSpan, decode
from ruida_re.transport import encode_datagram


ROOT = Path(__file__).resolve().parents[1]
RD_PATH = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"


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
