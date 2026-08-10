"""Tests for the language-neutral conformance vectors."""

from __future__ import annotations

from contextlib import redirect_stderr
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from ruida_re.catalog import build_catalog, catalog_json
from ruida_re.codec import swizzle, unswizzle
from ruida_re.conformance import (
    CONFORMANCE_SCHEMA,
    build_conformance,
    conformance_json,
    main,
)
from ruida_re.fields import (
    AbsoluteMmField,
    ByteField,
    BytesField,
    ColorField,
    CStringField,
    PackedBytes8Field,
    PowerField,
    RelativeMmField,
    S7Field,
    S35Field,
    ScaledS32Field,
    ScaledU35Field,
    U14Field,
    U35Field,
)
from ruida_re.program import KnownCommand, decode
from ruida_re.registry import get_registry
from ruida_re.transport import (
    checksum,
    decode_datagram,
    encode_datagram,
)


ROOT = Path(__file__).resolve().parents[1]
VECTOR_PATH = ROOT / "spec/conformance-v1.json"
SCHEMA_PATH = ROOT / "schemas/conformance-v1.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ConformanceTest(unittest.TestCase):
    def test_checked_in_vectors_are_synchronized(self) -> None:
        self.assertEqual(
            VECTOR_PATH.read_text(encoding="utf-8"),
            conformance_json(),
        )
        self.assertEqual(load_json(VECTOR_PATH), build_conformance())

    def test_every_published_field_codec_has_executable_vectors(self) -> None:
        document = load_json(VECTOR_PATH)
        published = {
            codec["id"] for codec in build_catalog()["codecs"]
        }
        covered = {
            vector["codec"] for vector in document["field_vectors"]
        }
        self.assertEqual(covered, published)
        for vector in document["field_vectors"]:
            with self.subTest(vector=vector["id"]):
                field = self.field_codec(
                    vector["codec"],
                    vector["parameters"],
                )
                wire = bytes.fromhex(vector["wire_hex"])
                self.assertEqual(
                    field.encode(vector["encode_json_value"]),
                    wire,
                )
                decoded, end = field.decode(wire, 0)
                self.assertEqual(end, len(wire))
                self.assertEqual(decoded, vector["decode_json_value"])
                self.assertEqual(field.encode(decoded), wire)
                self.assertTrue(vector["canonical"])

    def test_swizzle_vectors_cover_every_input_byte(self) -> None:
        vectors = load_json(VECTOR_PATH)["swizzle_vectors"]
        self.assertEqual(len(vectors), 1)
        vector = vectors[0]
        logical = bytes.fromhex(vector["logical_hex"])
        scrambled = bytes.fromhex(vector["scrambled_hex"])
        self.assertEqual(logical, bytes(range(256)))
        self.assertEqual(swizzle(logical, vector["magic"]), scrambled)
        self.assertEqual(unswizzle(scrambled, vector["magic"]), logical)

    def test_job_checksum_vector_matches_the_lightburn_fixture(self) -> None:
        vectors = load_json(VECTOR_PATH)["job_checksum_vectors"]
        self.assertEqual(len(vectors), 1)
        vector = vectors[0]
        logical = bytes.fromhex(vector["logical_without_checksum_hex"])
        self.assertEqual(sum(logical), vector["checksum_integer"])
        self.assertEqual(
            sha256(logical).hexdigest(),
            vector["logical_without_checksum_sha256"],
        )
        checksum_spec = get_registry("job").name("file_checksum")
        self.assertIsNotNone(checksum_spec)
        self.assertEqual(
            checksum_spec.encode({"value": vector["checksum_integer"]}),
            bytes.fromhex(vector["encoded_checksum_command_hex"]),
        )
        self.assertEqual(vector["checksum_integer"], 21493)
        self.assertEqual(
            vector["encoded_checksum_command_hex"],
            "e5050000012775",
        )

        fixture = vector["evidence"]["fixture"]
        fixture_path = ROOT / fixture["path"]
        fixture_bytes = fixture_path.read_bytes()
        self.assertEqual(sha256(fixture_bytes).hexdigest(), fixture["sha256"])
        program = decode(fixture_bytes)
        registry = get_registry("job")
        fixture_without_checksum = b"".join(
            record.encode(registry)
            for record in program.records
            if not (
                isinstance(record, KnownCommand)
                and record.name == "file_checksum"
            )
        )
        self.assertEqual(fixture_without_checksum, logical)

    def test_udp_vectors_reproduce_directional_framing(self) -> None:
        vectors = load_json(VECTOR_PATH)["udp_vectors"]
        self.assertEqual(
            {vector["context"] for vector in vectors},
            {"job", "request", "reply"},
        )
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                logical = bytes.fromhex(vector["logical_hex"])
                scrambled = bytes.fromhex(vector["scrambled_hex"])
                datagram = bytes.fromhex(vector["datagram_hex"])
                self.assertEqual(
                    swizzle(logical, vector["magic"]),
                    scrambled,
                )
                self.assertEqual(
                    encode_datagram(
                        logical,
                        vector["context"],
                        vector["magic"],
                    ),
                    datagram,
                )
                self.assertEqual(
                    decode_datagram(
                        datagram,
                        vector["context"],
                        vector["magic"],
                    ),
                    logical,
                )
                if vector["context"] in ("job", "request"):
                    expected = checksum(scrambled).to_bytes(2, "big")
                    self.assertEqual(vector["checksum_hex"], expected.hex())
                    self.assertEqual(datagram, expected + scrambled)
                else:
                    self.assertIsNone(vector["checksum_hex"])
                    self.assertEqual(datagram, scrambled)
        request = next(
            vector for vector in vectors if vector["context"] == "request"
        )
        reply = next(
            vector for vector in vectors if vector["context"] == "reply"
        )
        job = next(
            vector for vector in vectors if vector["context"] == "job"
        )
        self.assertEqual(job["datagram_hex"], "006060")
        self.assertEqual(request["datagram_hex"], "01efd4898909")
        self.assertEqual(reply["datagram_hex"], "c6")

    def test_fixture_evidence_is_content_addressed_and_present(self) -> None:
        document = load_json(VECTOR_PATH)
        logical_by_fixture = {}
        for vector in document["field_vectors"]:
            fixture = vector["evidence"].get("fixture")
            if fixture is None:
                continue
            path = ROOT / fixture["path"]
            raw = path.read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), fixture["sha256"])
            logical = logical_by_fixture.get(path)
            if logical is None:
                logical = decode(raw).encode(checksum_policy="preserve")
                logical = unswizzle(logical)
                logical_by_fixture[path] = logical
            self.assertIn(bytes.fromhex(vector["wire_hex"]), logical)

    def test_evidence_references_resolve_and_vector_ids_are_unique(
        self,
    ) -> None:
        document = load_json(VECTOR_PATH)
        sources = {source["id"] for source in document["sources"]}
        identifiers = []
        groups = (
            "field_vectors",
            "swizzle_vectors",
            "job_checksum_vectors",
            "udp_vectors",
        )
        for group in groups:
            for vector in document[group]:
                identifiers.append(vector["id"])
                self.assertLessEqual(
                    set(vector["evidence"]["source_ids"]),
                    sources,
                )
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(
            document["catalog"]["sha256"],
            sha256(catalog_json().encode("utf-8")).hexdigest(),
        )

    def test_schema_is_versioned_and_self_contained(self) -> None:
        schema = load_json(SCHEMA_PATH)
        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertEqual(
            schema["$id"],
            "urn:ruida-re:schema:conformance:v1",
        )
        self.assertEqual(
            schema["properties"]["schema"]["const"],
            CONFORMANCE_SCHEMA,
        )
        schema_codecs = set(schema["$defs"]["codecId"]["enum"])
        catalog_codecs = {
            codec["id"] for codec in build_catalog()["codecs"]
        }
        self.assertEqual(schema_codecs, catalog_codecs)
        self.assert_local_references_resolve(schema, schema)

    def test_artifact_validates_against_draft_2020_12_schema(self) -> None:
        schema = load_json(SCHEMA_PATH)
        document = load_json(VECTOR_PATH)
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda error: list(error.path),
        )
        self.assertEqual(errors, [])

    def test_generator_check_and_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "conformance.json"
            with patch.object(
                sys,
                "argv",
                ["ruida-conformance", "--output", str(path)],
            ):
                main()
            with patch.object(
                sys,
                "argv",
                ["ruida-conformance", "--check", str(path)],
            ):
                main()
            with patch.object(
                sys,
                "argv",
                ["ruida-conformance", "--output", str(path)],
            ):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main()
            self.assertEqual(raised.exception.code, 2)
            path.write_text("{}\n", encoding="utf-8")
            with patch.object(
                sys,
                "argv",
                ["ruida-conformance", "--check", str(path)],
            ):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main()
            self.assertEqual(raised.exception.code, 1)

    def field_codec(self, identifier, parameters):
        if identifier == "absolute-mm-s32":
            return AbsoluteMmField("value")
        if identifier == "bytes-7bit":
            return BytesField("value", parameters["size"])
        if identifier == "color-bgr-u35":
            return ColorField("value")
        if identifier == "cstring-7bit":
            return CStringField("value")
        if identifier == "packed-bytes8-u35":
            return PackedBytes8Field("value")
        if identifier == "power-u14":
            return PowerField("value")
        if identifier == "relative-mm-s14":
            return RelativeMmField("value")
        if identifier == "s7":
            return S7Field("value")
        if identifier == "s35":
            return S35Field("value")
        if identifier == "scaled-s32":
            return ScaledS32Field("value", parameters["scale"])
        if identifier == "scaled-u35":
            return ScaledU35Field("value", parameters["scale"])
        if identifier == "u7":
            return ByteField("value")
        if identifier == "u14":
            return U14Field("value")
        if identifier == "u35":
            return U35Field("value")
        self.fail(f"Unknown conformance codec {identifier}")

    def assert_local_references_resolve(self, root, node) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if reference is not None:
                prefix = "#/$defs/"
                self.assertTrue(reference.startswith(prefix))
                self.assertIn(reference.removeprefix(prefix), root["$defs"])
            for value in node.values():
                self.assert_local_references_resolve(root, value)
        elif isinstance(node, list):
            for value in node:
                self.assert_local_references_resolve(root, value)


if __name__ == "__main__":
    unittest.main()
