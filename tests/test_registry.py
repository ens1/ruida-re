"""Symmetry checks for every declarative command specification."""

from __future__ import annotations

from collections import Counter
import unittest
from pathlib import Path

from ruida_re.program import KnownCommand, decode
from ruida_re.registry import (
    DEFAULT_REGISTRY,
    LIGHTBURN_OBSERVED,
    REGISTRIES,
    get_registry,
)

from sample_values import sample_value


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "fixtures/lightburn-2.1.03"


class RegistryTest(unittest.TestCase):
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
                "uncited-hypothesis": 109,
                "reported": 3,
                "fixture-observed": 65,
                "external-fixture-observed": 1,
                "conflicting-reports": 6,
            },
        )
        self.assertIsNotNone(request.name("keep_alive_request"))
        self.assertIsNone(reply.name("document_name_reply"))
        hypothesis = reply.name("mainboard_version_reply_hypothesis")
        self.assertIsNotNone(hypothesis)
        self.assertEqual(hypothesis.semantic_evidence, "disputed")

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
