"""Tests for the stable embedding API."""

from __future__ import annotations

import unittest
from pathlib import Path

from ruida_re.api import RuidaCodec
from ruida_re.codec import unswizzle
from ruida_re.program import KnownCommand, Program
from ruida_re.specs import CommandRegistry, CommandSpec


ROOT = Path(__file__).resolve().parents[1]
RD_PATH = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"


class RuidaCodecTest(unittest.TestCase):
    def test_decodes_and_reencodes_a_real_file(self) -> None:
        codec = RuidaCodec()
        raw = RD_PATH.read_bytes()
        program = codec.decode(raw)
        self.assertEqual(codec.encode(program), raw)

    def test_constructs_validated_commands_by_name(self) -> None:
        codec = RuidaCodec()
        move = codec.command("move_absolute", x_mm=-1.0, y_mm=2.0)
        eof = codec.command("end_of_file")
        program = codec.program([move, eof])
        self.assertEqual(program.records[0].offset, 0)
        self.assertEqual(program.records[1].offset, 11)
        encoded = codec.encode(program)
        decoded = codec.decode(encoded, container="logical")
        self.assertEqual(
            [record.name for record in decoded.records],
            ["move_absolute", "end_of_file"],
        )
        self.assertEqual(
            decoded.records[0].values,
            {"x_mm": -1.0, "y_mm": 2.0},
        )

    def test_rejects_unknown_commands_and_invalid_fields(self) -> None:
        codec = RuidaCodec()
        with self.assertRaises(ValueError):
            codec.command("not_a_command")
        with self.assertRaises(ValueError):
            codec.command("select_layer", layer=128)

    def test_preserves_explicit_opaque_records(self) -> None:
        codec = RuidaCodec()
        raw = bytes.fromhex("d029")
        program = codec.program([codec.opaque(raw)])
        self.assertEqual(codec.encode(program), raw)

    def test_outbound_datagrams_round_trip_across_small_chunks(self) -> None:
        codec = RuidaCodec(context="request")
        records = [
            codec.command("get_setting", address=1),
            codec.command("keep_alive_request"),
        ]
        program = codec.program(records)
        datagrams = codec.encode_datagrams(program, mtu=2)
        self.assertGreater(len(datagrams), 1)
        self.assertTrue(all(len(datagram) <= 4 for datagram in datagrams))
        decoded = codec.decode_datagrams(datagrams)
        self.assertEqual(
            [record.name for record in decoded.records],
            ["get_setting", "keep_alive_request"],
        )

    def test_real_job_is_independent_of_packet_boundaries(self) -> None:
        codec = RuidaCodec()
        raw = RD_PATH.read_bytes()
        program = codec.decode(raw)
        logical = unswizzle(raw)
        for mtu in (1, 2, 3, 7, 31, 127, 1470):
            with self.subTest(mtu=mtu):
                packets = codec.encode_datagrams(program, mtu=mtu)
                decoded = codec.decode_datagrams(packets)
                self.assertEqual(codec.encode(decoded), logical)

    def test_reply_datagrams_have_no_checksum_prefix(self) -> None:
        codec = RuidaCodec(context="reply")
        program = codec.program([codec.command("acknowledge")])
        datagrams = codec.encode_datagrams(program)
        self.assertEqual(datagrams, (bytes.fromhex("c6"),))
        decoded = codec.decode_datagrams(datagrams)
        self.assertIsInstance(decoded.records[0], KnownCommand)
        self.assertEqual(decoded.records[0].name, "acknowledge")

    def test_stream_decoder_uses_the_codec_context(self) -> None:
        decoder = RuidaCodec(context="reply").stream_decoder()
        self.assertEqual(decoder.feed(bytes.fromhex("cc")), [])
        records = decoder.finish()
        self.assertEqual(records[0].name, "acknowledge")

    def test_command_names_follow_the_selected_context(self) -> None:
        job = RuidaCodec(context="job")
        reply = RuidaCodec(context="reply")
        self.assertIn("move_absolute", job.command_names)
        self.assertNotIn("move_absolute", reply.command_names)
        self.assertIn("acknowledge", reply.command_names)

    def test_rejects_programs_from_another_codec_configuration(self) -> None:
        job = RuidaCodec()
        request = RuidaCodec(context="request")
        program = job.program([job.command("end_of_file")])
        with self.assertRaisesRegex(ValueError, "context"):
            request.encode(program)
        with self.assertRaisesRegex(ValueError, "context"):
            request.encode_datagrams(program)
        alternate_magic = RuidaCodec(magic=0x11)
        with self.assertRaisesRegex(ValueError, "magic"):
            alternate_magic.encode(program)
        with self.assertRaisesRegex(ValueError, "magic"):
            alternate_magic.encode_datagrams(program)

    def test_rejects_boolean_magic(self) -> None:
        with self.assertRaisesRegex(ValueError, "Magic"):
            RuidaCodec(magic=True)

    def test_custom_registry_survives_program_json(self) -> None:
        registry = CommandRegistry(
            [CommandSpec(b"\x80", "dialect_command")]
        )
        codec = RuidaCodec(registry=registry)
        program = codec.decode(b"\x80", container="logical")
        value = program.to_json()
        restored = Program.from_json(value, registry=registry)
        self.assertEqual(codec.encode(restored), b"\x80")


if __name__ == "__main__":
    unittest.main()
