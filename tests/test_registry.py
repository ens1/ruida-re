"""Symmetry checks for every declarative command specification."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import unittest

from ruida_re.codec import swizzle, unswizzle
from ruida_re.fields import ByteField
from ruida_re.program import KnownCommand, decode
from ruida_re.registry import (
    DEFAULT_REGISTRY,
    LIGHTBURN_OBSERVED,
    REGISTRIES,
    SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
    get_registry,
)
from ruida_re.specs import CommandRegistry, CommandSpec

from sample_values import sample_value


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "fixtures/lightburn-2.1.03"


class RegistryTest(unittest.TestCase):
    def test_registry_rejects_invalid_or_duplicate_field_names(self) -> None:
        for fields in (
            (ByteField("BadName"),),
            (ByteField("value"), ByteField("value")),
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    CommandRegistry(
                        [CommandSpec(b"\x80", "example", fields)]
                    )

    def test_registry_rejects_an_invalid_command_name(self) -> None:
        with self.assertRaises(ValueError):
            CommandRegistry([CommandSpec(b"\x80", "BadName")])

    def test_registry_rejects_unknown_evidence_classifications(self) -> None:
        invalid = (
            CommandSpec(
                b"\x80",
                "example",
                shape_evidence="future-shape",
            ),
            CommandSpec(
                b"\x80",
                "example",
                semantic_evidence="future-semantics",
            ),
        )
        for spec in invalid:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    CommandRegistry([spec])

    def test_registry_validates_declarative_reply_contracts(self) -> None:
        invalid = (
            CommandSpec(
                b"\x80",
                "example",
                reply_behavior="none",
                reply_commands=("setting_reply",),
            ),
            CommandSpec(
                b"\x80",
                "example",
                reply_behavior="data",
                reply_commands=("BadName",),
            ),
            CommandSpec(
                b"\x80",
                "example",
                reply_behavior="data",
                reply_field_matches=(("bad-field", "address"),),
            ),
        )
        for spec in invalid:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    CommandRegistry([spec])

    def test_every_command_spec_is_symmetric(self) -> None:
        for context, registry in REGISTRIES.items():
            for spec in registry:
                with self.subTest(context=context, command=spec.name):
                    values = {
                        field.name: sample_value(field)
                        for field in spec.fields
                    }
                    encoded = spec.encode(values)
                    decoded, end = spec.decode(encoded, 0)
                    self.assertEqual(end, len(encoded))
                    self.assertEqual(spec.encode(decoded), encoded)
                    for name, value in values.items():
                        if isinstance(value, float):
                            self.assertAlmostEqual(
                                decoded[name],
                                value,
                                places=2,
                            )
                        else:
                            self.assertEqual(decoded[name], value)

    def test_context_registries_are_distinct(self) -> None:
        self.assertIsNot(get_registry("job"), get_registry("request"))
        self.assertIsNone(get_registry("request").name("move_absolute"))
        self.assertIsNone(get_registry("job").name("acknowledge"))
        self.assertIsNotNone(get_registry("reply").name("acknowledge"))

    def test_context_scope_and_evidence_are_explicit(self) -> None:
        job = list(get_registry("job"))
        request = get_registry("request")
        reply = get_registry("reply")
        self.assertEqual(len(job), 184)
        self.assertEqual(len(list(request)), 74)
        self.assertEqual(len(list(reply)), 10)
        self.assertEqual(
            Counter(spec.shape_evidence for spec in job),
            {
                "uncited-hypothesis": 101,
                "reported": 4,
                "fixture-observed": 72,
                "external-fixture-observed": 1,
                "conflicting-reports": 6,
            },
        )
        self.assertIsNotNone(request.name("keep_alive_request"))
        read = request.name("get_setting")
        self.assertIsNotNone(read)
        self.assertEqual(read.shape_evidence, "hardware-observed")
        self.assertEqual(read.semantic_evidence, "hardware-observed")
        self.assertEqual(read.controller_effect, "read-only")
        self.assertEqual(read.reply_behavior, "data")
        self.assertEqual(read.reply_commands, ("setting_reply",))
        self.assertEqual(
            read.reply_field_matches,
            (("address", "address"),),
        )
        self.assertEqual(len(read.shape_sources), 3)
        self.assertEqual(len(read.semantic_sources), 3)
        self.assertIn(
            SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
            read.semantic_sources,
        )
        job_read = get_registry("job").name("get_setting")
        self.assertEqual(job_read.shape_evidence, "reported")
        self.assertEqual(job_read.semantic_evidence, "reported")
        self.assertNotIn(
            SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
            job_read.shape_sources,
        )
        write = request.name("set_setting")
        self.assertEqual(write.controller_effect, "state-changing")
        self.assertEqual(write.reply_behavior, "none")
        self.assertEqual(write.reply_commands, ())
        self.assertEqual(write.reply_field_matches, ())
        self.assertNotEqual(write.shape_evidence, "hardware-observed")
        self.assertNotEqual(write.semantic_evidence, "hardware-observed")
        self.assertIsNone(reply.name("document_name_reply"))
        hypothesis = reply.name("mainboard_version_reply_hypothesis")
        self.assertIsNotNone(hypothesis)
        self.assertEqual(hypothesis.semantic_evidence, "disputed")

    def test_hardware_setting_capture_matches_registry(self) -> None:
        request = get_registry("request").name("get_setting")
        reply = get_registry("reply").name("setting_reply")
        self.assertIsNotNone(request)
        self.assertIsNotNone(reply)
        request_logical = bytes.fromhex("da000005")
        request_wire = bytes.fromhex("d489890d")
        reply_logical = bytes.fromhex("da0100050000122760")
        reply_wire = bytes.fromhex("d409890d89899b2fe9")
        self.assertEqual(request.encode({"address": 5}), request_logical)
        self.assertEqual(swizzle(request_logical, 0x88), request_wire)
        self.assertEqual(unswizzle(reply_wire, 0x88), reply_logical)
        values, end = reply.decode(reply_logical, 0)
        self.assertEqual(end, len(reply_logical))
        self.assertEqual(values, {"address": 5, "value": 300000})
        self.assertEqual(reply.shape_evidence, "hardware-observed")
        self.assertEqual(reply.semantic_evidence, "hardware-observed")
        self.assertIn(
            SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
            reply.shape_sources,
        )

    def test_non_hypothesis_evidence_has_specific_sources(self) -> None:
        for context, registry in REGISTRIES.items():
            for spec in registry:
                with self.subTest(context=context, command=spec.name):
                    if spec.shape_evidence != "uncited-hypothesis":
                        self.assertTrue(spec.shape_sources)
                    if spec.semantic_evidence != "uncited-hypothesis":
                        self.assertTrue(spec.semantic_sources)

    def test_fixture_evidence_is_explicit(self) -> None:
        marked = {
            spec.opcode.hex()
            for spec in DEFAULT_REGISTRY
            if spec.shape_evidence == "fixture-observed"
        }
        self.assertEqual(marked, LIGHTBURN_OBSERVED)
        observed = set()
        for path in FIXTURE_ROOT.rglob("*.rd"):
            for record in decode(path.read_bytes()).records:
                if isinstance(record, KnownCommand):
                    observed.add(record.opcode)
                    self.assertEqual(
                        record.shape_evidence,
                        "fixture-observed",
                    )
        self.assertEqual(observed, LIGHTBURN_OBSERVED)


if __name__ == "__main__":
    unittest.main()
