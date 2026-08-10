"""Tests for packaged protocol catalogs and schemas."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from ruida_re.resources import (
    ARTIFACTS,
    CATALOG_SCHEMA_V1,
    CATALOG_V1,
    CONFORMANCE_SCHEMA_V1,
    CONFORMANCE_V1,
    PROGRAM_SCHEMA_V1,
    TRANSCRIPT_SCHEMA_V1,
    artifact,
    artifact_path,
    read_artifact_bytes,
    read_artifact_json,
    read_artifact_text,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ARTIFACTS = {
    CATALOG_V1: ROOT / "spec/catalog-v1.json",
    CATALOG_SCHEMA_V1: ROOT / "schemas/catalog-v1.schema.json",
    CONFORMANCE_V1: ROOT / "spec/conformance-v1.json",
    CONFORMANCE_SCHEMA_V1: ROOT / "schemas/conformance-v1.schema.json",
    PROGRAM_SCHEMA_V1: ROOT / "schemas/program-v1.schema.json",
    TRANSCRIPT_SCHEMA_V1: ROOT / "schemas/transcript-v1.schema.json",
}


class ResourcesTest(unittest.TestCase):
    def test_every_artifact_is_packaged_and_synchronized(self) -> None:
        self.assertEqual(set(ARTIFACTS), set(SOURCE_ARTIFACTS))
        for name, source in SOURCE_ARTIFACTS.items():
            with self.subTest(name=name):
                expected = source.read_bytes()
                self.assertTrue(artifact(name).is_file())
                self.assertEqual(read_artifact_bytes(name), expected)
                self.assertEqual(
                    read_artifact_text(name),
                    expected.decode("utf-8"),
                )

    def test_artifact_path_is_available_to_path_consumers(self) -> None:
        with artifact_path(CATALOG_SCHEMA_V1) as path:
            self.assertTrue(path.is_file())
            self.assertEqual(
                path.read_bytes(),
                SOURCE_ARTIFACTS[CATALOG_SCHEMA_V1].read_bytes(),
            )

    def test_packaged_json_identifiers_are_versioned(self) -> None:
        catalog = read_artifact_json(CATALOG_V1)
        self.assertEqual(catalog["schema"], "ruida-re.catalog.v1")
        conformance = read_artifact_json(CONFORMANCE_V1)
        self.assertEqual(
            conformance["schema"],
            "ruida-re.conformance.v1",
        )
        expected_ids = {
            CATALOG_SCHEMA_V1: "urn:ruida-re:schema:catalog:v1",
            CONFORMANCE_SCHEMA_V1: (
                "urn:ruida-re:schema:conformance:v1"
            ),
            PROGRAM_SCHEMA_V1: "urn:ruida-re:schema:program:v1",
            TRANSCRIPT_SCHEMA_V1: "urn:ruida-re:schema:transcript:v1",
        }
        for name, identifier in expected_ids.items():
            with self.subTest(name=name):
                self.assertEqual(read_artifact_json(name)["$id"], identifier)

    def test_unknown_artifact_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            artifact("../pyproject.toml")

    def test_distribution_configuration_declares_resources(self) -> None:
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-catalog"],
            "ruida_re.catalog:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-conformance"],
            "ruida_re.conformance:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-capability-fixture"],
            "ruida_re.capability_fixture:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-controller"],
            "ruida_re.controller_cli:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-raster-fixture"],
            "ruida_re.raster_fixture:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-lightburn-profile"],
            "ruida_re.lightburn_profile:main",
        )
        self.assertEqual(
            project["project"]["scripts"]["ruida-experiment"],
            "ruida_re.experiment:main",
        )
        optional = project["project"]["optional-dependencies"]
        self.assertIn("pyserial>=3.5", optional["serial"])
        self.assertIn("jsonschema>=4.23", optional["test"])
        package_data = project["tool"]["setuptools"]["package-data"]
        self.assertIn("data/schemas/*.json", package_data["ruida_re"])
        self.assertIn("data/spec/*.json", package_data["ruida_re"])

    def test_packaged_json_is_valid_utf8_json(self) -> None:
        for name in ARTIFACTS:
            with self.subTest(name=name):
                self.assertEqual(
                    json.loads(read_artifact_text(name)),
                    read_artifact_json(name),
                )


if __name__ == "__main__":
    unittest.main()
