"""Tests for the language-neutral JSON numeric domain."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
SAFE_INTEGER = 9_007_199_254_740_991


def load_schema(name: str) -> dict:
    return json.loads(
        (SCHEMA_DIR / name).read_text(encoding="utf-8")
    )


def numeric_declarations(value, path=()):
    if isinstance(value, dict):
        declared = value.get("type")
        types = {declared} if isinstance(declared, str) else set(
            declared or ()
        )
        if types & {"integer", "number"}:
            yield path, value
        for key, child in value.items():
            yield from numeric_declarations(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from numeric_declarations(child, (*path, index))


class SchemaNumericDomainTest(unittest.TestCase):
    def test_schemas_are_valid_draft_2020_12(self) -> None:
        for schema_path in sorted(SCHEMA_DIR.glob("*.json")):
            with self.subTest(schema=schema_path.name):
                Draft202012Validator.check_schema(
                    load_schema(schema_path.name)
                )

    def test_every_numeric_declaration_has_interoperable_bounds(
        self,
    ) -> None:
        for schema_path in sorted(SCHEMA_DIR.glob("*.json")):
            schema = load_schema(schema_path.name)
            for path, declaration in numeric_declarations(schema):
                label = "/".join(map(str, path))
                with self.subTest(schema=schema_path.name, path=label):
                    lower = declaration.get(
                        "minimum",
                        declaration.get("exclusiveMinimum"),
                    )
                    upper = declaration.get(
                        "maximum",
                        declaration.get("exclusiveMaximum"),
                    )
                    self.assertIsNotNone(lower)
                    self.assertIsNotNone(upper)
                    self.assertGreaterEqual(lower, -SAFE_INTEGER)
                    self.assertLessEqual(upper, SAFE_INTEGER)

    def test_program_metadata_and_values_use_safe_domain(self) -> None:
        schema = load_schema("program-v1.schema.json")
        validator = Draft202012Validator(schema)
        document = {
            "schema": "ruida-re.program.v1",
            "magic": 136,
            "context": "job",
            "container": "logical",
            "header": "",
            "records": [
                {
                    "kind": "command",
                    "offset": SAFE_INTEGER,
                    "opcode": "d7",
                    "name": "future_command",
                    "values": {
                        "lower": -SAFE_INTEGER,
                        "upper": SAFE_INTEGER,
                    },
                    "shape_evidence": "uncited-hypothesis",
                    "semantic_evidence": "unverified",
                }
            ],
            "issues": [],
            "source_checksum_basis": SAFE_INTEGER,
        }
        validator.validate(document)

        cases = (
            (("source_checksum_basis",), SAFE_INTEGER + 1),
            (("records", 0, "offset"), SAFE_INTEGER + 1),
            (
                ("records", 0, "values", "upper"),
                SAFE_INTEGER + 1,
            ),
            (
                ("records", 0, "values", "lower"),
                -SAFE_INTEGER - 1,
            ),
        )
        for path, value in cases:
            with self.subTest(path=path):
                invalid = deepcopy(document)
                target = invalid
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValidationError):
                    validator.validate(invalid)

    def test_transcript_timestamp_uses_safe_domain(self) -> None:
        schema = load_schema("transcript-v1.schema.json")
        validator = Draft202012Validator(schema)
        document = {
            "schema": "ruida-re.transcript.v1",
            "datagrams": [
                {
                    "direction": "inbound",
                    "context": "reply",
                    "timestamp": SAFE_INTEGER,
                    "raw": "c6",
                    "program": None,
                    "issues": ["not decoded"],
                }
            ],
        }
        validator.validate(document)
        document["datagrams"][0]["timestamp"] = -SAFE_INTEGER
        validator.validate(document)
        for value in (SAFE_INTEGER + 1, -SAFE_INTEGER - 1):
            with self.subTest(value=value):
                document["datagrams"][0]["timestamp"] = value
                with self.assertRaises(ValidationError):
                    validator.validate(document)

    def test_transcript_embeds_the_exact_program_schema(self) -> None:
        program = load_schema("program-v1.schema.json")
        transcript = load_schema("transcript-v1.schema.json")
        self.assertEqual(transcript["$defs"]["program"], program)


if __name__ == "__main__":
    unittest.main()
