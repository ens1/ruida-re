"""Tests for the checked advanced-capability evidence publication."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ruida_re.capability_fixture import (
    CASES,
    PROMOTED_FAMILIES,
    PROMOTION_SCHEMA,
    RotaryProject,
    controlled_differences,
    promote,
    sanitize_profile_document,
    validate_cases,
)
from ruida_re.experiment import load_manifest
from ruida_re.program import KnownCommand, RawSpan, decode

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/lightburn-2.1.03/capabilities"
MANIFEST_PATH = FIXTURE / "capabilities.json"
SOURCE_MANIFEST_SHA256 = (
    "32a2059b3a610787f8bb21cfbc0c9481e234deb7a68476866e267f19d2dcf062"
)
PROFILE_ORIGINAL_SHA256 = {
    "research-enable-z.lbdev": (
        "d9afa5c48b99608728b2e75a1db13b9b5f31c78c5dd9cfcca4a14555c37c2012"
    ),
    "research-laser-1-fiber.lbdev": (
        "d7005bc3d8a649c0a52d22a71f55ed8f8c64f16447cdd058f715868c2126cf82"
    ),
    "research-laser-1-rf-tube.lbdev": (
        "ce78e1f1d1bd49a50734f35c327425f6c2df1cac7670c1904ff51664ce4d855d"
    ),
    "research-laser-2-enabled.lbdev": (
        "12f1abbda0bbb3927a0d918ee92cb0a9cc2de127808783902231f5c99acfdaf6"
    ),
    "research-save-rotary-config.lbdev": (
        "d2a5dfbfb78e95dc6aa3e9212afaa4245f94c9ea7a6f5c37be717d56819ed919"
    ),
    "ruida-644xs-active.lbdev": (
        "4794289587d1d4f3dfa8a94fb717aae367f2a7bb1deef9027f7a086885071487"
    ),
}
MATRIX_ORIGINAL_SHA256 = (
    "3db182e00925b7dd055d0a26d23edd7900b905bc236e36969990d0e69618b9cb"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Capability manifest must be an object")
    return value


def _cases(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = manifest["cases"]
    if not isinstance(values, list):
        raise TypeError("Capability cases must be an array")
    return {
        item["identifier"]: item
        for item in values
        if isinstance(item, dict) and isinstance(item.get("identifier"), str)
    }


def _json_controls(value: object) -> object:
    return json.loads(json.dumps(value))


class CapabilityPublicationTest(unittest.TestCase):
    def test_file_set_is_exact(self) -> None:
        captured = [
            case for case in CASES if not isinstance(case.project, RotaryProject)
        ]
        expected = {Path("capabilities.json")}
        for case in captured:
            expected.add(Path(f"{case.identifier}.lbrn2"))
            expected.add(Path(f"{case.identifier}.rd"))
        for family in PROMOTED_FAMILIES:
            expected.add(Path(f"{family}.experiment.json"))
            expected.add(Path(f"{family}.report.json"))
        for filename in PROFILE_ORIGINAL_SHA256:
            expected.add(Path("profiles") / filename)
        expected.add(Path("profiles/lightburn-profile-matrix.json"))
        actual = {
            path.relative_to(FIXTURE) for path in FIXTURE.rglob("*") if path.is_file()
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 114)

    def test_case_artifacts_match_all_recorded_hashes(self) -> None:
        manifest = _manifest()
        cases = _cases(manifest)
        self.assertEqual(set(cases), {case.identifier for case in CASES})
        for case in CASES:
            item = cases[case.identifier]
            files = item["files"]
            if not isinstance(files, dict):
                raise TypeError("Case files must be an object")
            if isinstance(case.project, RotaryProject):
                self.assertEqual(files, {})
                continue
            self.assertEqual(
                set(files),
                {f"{case.identifier}.lbrn2", f"{case.identifier}.rd"},
            )
            capture = item["capture"]
            if not isinstance(capture, dict):
                raise TypeError("Case capture must be an object")
            for filename, metadata in files.items():
                if not isinstance(metadata, dict):
                    raise TypeError("File metadata must be an object")
                path = FIXTURE / filename
                self.assertEqual(_sha256(path), metadata["sha256"])
                self.assertEqual(path.stat().st_size, metadata["size"])
            self.assertEqual(
                capture["project_sha256"],
                files[f"{case.identifier}.lbrn2"]["sha256"],
            )
            self.assertEqual(
                capture["rd_sha256"],
                files[f"{case.identifier}.rd"]["sha256"],
            )

    def test_all_machine_files_decode_without_loss(self) -> None:
        for path in sorted(FIXTURE.glob("c*.rd")):
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                program = decode(
                    raw,
                    magic=0x88,
                    context="job",
                    container="rd",
                )
                self.assertEqual(program.issues, [])
                self.assertFalse(
                    any(isinstance(record, RawSpan) for record in program.records)
                )
                self.assertEqual(program.encode(), raw)
                checksums = [
                    record.values["value"]
                    for record in program.records
                    if isinstance(record, KnownCommand)
                    and record.name == "file_checksum"
                ]
                self.assertTrue(checksums)
                self.assertTrue(
                    all(value == program.source_checksum_basis for value in checksums)
                )

    def test_controls_and_comparisons_are_one_variable_inputs(self) -> None:
        validate_cases()
        manifest_cases = _cases(_manifest())
        indexed = {case.identifier: case for case in CASES}
        for case in CASES:
            with self.subTest(case=case.identifier):
                item = manifest_cases[case.identifier]
                controls = item["controls"]
                if not isinstance(controls, dict):
                    raise TypeError("Case controls must be an object")
                project_kind = controls["project_kind"]
                expected_controls = {
                    "project_kind": project_kind,
                    **asdict(case.project),
                }
                self.assertEqual(controls, _json_controls(expected_controls))
                if case.baseline is None:
                    self.assertIsNone(item["comparison"])
                    continue
                comparison = item["comparison"]
                if not isinstance(comparison, dict):
                    raise TypeError("Case comparison must be an object")
                self.assertEqual(comparison["baseline"], case.baseline)
                self.assertEqual(
                    comparison["independent_variable"],
                    case.independent_variable,
                )
                self.assertEqual(
                    comparison["controlled_differences"],
                    [case.independent_variable],
                )
                self.assertEqual(
                    controlled_differences(indexed[case.baseline], case),
                    {case.independent_variable},
                )

    def test_profiles_are_sanitized_with_exact_origin_provenance(self) -> None:
        manifest = _manifest()
        publication = manifest["publication"]
        if not isinstance(publication, dict):
            raise TypeError("Publication metadata must be an object")
        self.assertEqual(publication["schema"], PROMOTION_SCHEMA)
        self.assertEqual(
            publication["source_manifest_sha256"],
            SOURCE_MANIFEST_SHA256,
        )
        values = publication["profiles"]
        if not isinstance(values, list):
            raise TypeError("Published profiles must be an array")
        profiles = {
            item["capture_filename"]: item for item in values if isinstance(item, dict)
        }
        self.assertEqual(set(profiles), set(PROFILE_ORIGINAL_SHA256))
        for filename, original_sha256 in PROFILE_ORIGINAL_SHA256.items():
            with self.subTest(profile=filename):
                item = profiles[filename]
                path = FIXTURE / item["path"]
                self.assertEqual(item["original_sha256"], original_sha256)
                self.assertEqual(item["published_sha256"], _sha256(path))
                self.assertNotEqual(item["original_sha256"], item["published_sha256"])
                self.assertEqual(
                    item["published_document_sha256"],
                    item["published_sha256"],
                )
                self.assertEqual(path.stat().st_size, item["published_size"])
                self.assertEqual(
                    item["redactions"],
                    [
                        {
                            "json_pointer": (
                                "/DeviceList/0/Settings/LastMachineFilePath"
                            ),
                            "operation": "remove",
                            "original_type": "string",
                            "original_utf8_size": 74,
                            "original_value_sha256": (
                                "06370a66e8a29e5589c6159e103a8d686ee47b8b277b"
                                "5193595a057223b409e7"
                            ),
                            "reason": "volatile-path-setting",
                        }
                    ],
                )
                profile = json.loads(path.read_text(encoding="utf-8"))
                device = profile["DeviceList"][0]
                self.assertEqual(device["ProfilePath"], "Ruida")
                self.assertNotIn(
                    "LastMachineFilePath",
                    device["Settings"],
                )

    def test_matrix_and_capture_profile_hashes_are_rewritten(self) -> None:
        manifest = _manifest()
        publication = manifest["publication"]
        if not isinstance(publication, dict):
            raise TypeError("Publication metadata must be an object")
        matrix_publication = publication["profile_matrix"]
        if not isinstance(matrix_publication, dict):
            raise TypeError("Profile matrix publication must be an object")
        self.assertEqual(
            matrix_publication["original_sha256"],
            MATRIX_ORIGINAL_SHA256,
        )
        matrix_path = FIXTURE / matrix_publication["path"]
        self.assertEqual(
            matrix_publication["published_sha256"],
            _sha256(matrix_path),
        )
        self.assertEqual(len(matrix_publication["rewrites"]), 12)
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        profile_publications = {
            item["capture_filename"]: item for item in publication["profiles"]
        }
        source_profile = profile_publications[matrix["source"]["filename"]]
        self.assertEqual(
            matrix["source"]["source_sha256"],
            source_profile["published_sha256"],
        )
        for variant in matrix["variants"]:
            profile = profile_publications[variant["filename"]]
            self.assertEqual(variant["variant_sha256"], profile["published_sha256"])
            self.assertEqual(variant["size"], profile["published_size"])

        for identifier, item in _cases(manifest).items():
            capture = item["capture"]
            if capture is None:
                continue
            if not isinstance(capture, dict):
                raise TypeError("Capture must be an object")
            evidence = capture["profile_evidence"]
            if not isinstance(evidence, dict):
                raise TypeError("Profile evidence must be an object")
            origin = evidence["capture_origin"]
            if not isinstance(origin, dict):
                raise TypeError("Capture origin must be an object")
            self.assertEqual(evidence["publication"]["stage"], "sanitized-publication")
            if evidence["kind"] == "lightburn-device-profile":
                profile = profile_publications[origin["filename"]]
                self.assertEqual(origin["evidence_sha256"], profile["original_sha256"])
                self.assertEqual(
                    evidence["evidence_sha256"], profile["published_sha256"]
                )
            else:
                profile = profile_publications[origin["profile_filename"]]
                self.assertEqual(origin["evidence_sha256"], MATRIX_ORIGINAL_SHA256)
                self.assertEqual(origin["profile_sha256"], profile["original_sha256"])
                self.assertEqual(
                    evidence["evidence_sha256"],
                    matrix_publication["published_sha256"],
                )
                self.assertEqual(
                    evidence["profile_sha256"], profile["published_sha256"]
                )
            self.assertEqual(
                capture["project_sha256"], item["files"][item["project"]]["sha256"]
            )
            self.assertEqual(
                capture["rd_sha256"], item["files"][item["expected_rd"]]["sha256"]
            )
            self.assertEqual(
                capture["export_attestation"],
                {
                    "controller_connection": "not-attested",
                    "job_transmitted": False,
                    "lightburn_launched": True,
                    "machine_file_action": "save-rd",
                },
                identifier,
            )

    def test_no_personal_paths_are_published(self) -> None:
        forbidden = (
            b"/Users/",
            b"tyler",
            b"Library/Preferences",
            b"Software/Personal",
        )
        for path in FIXTURE.rglob("*"):
            if not path.is_file():
                continue
            with self.subTest(path=path.relative_to(FIXTURE)):
                content = path.read_bytes().lower()
                for token in forbidden:
                    self.assertNotIn(token.lower(), content)

    def test_experiments_and_reports_are_self_consistent(self) -> None:
        for family in PROMOTED_FAMILIES:
            with self.subTest(family=family):
                experiment_path = FIXTURE / f"{family}.experiment.json"
                report_path = FIXTURE / f"{family}.report.json"
                experiment = load_manifest(experiment_path)
                self.assertEqual(experiment.capability, family)
                self.assertTrue(experiment.strict)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertTrue(report["valid"])
                self.assertTrue(report["strict"])
                self.assertEqual(report["manifest"], experiment_path.name)
                self.assertEqual(report["manifest_sha256"], _sha256(experiment_path))
                self.assertTrue(report["captures"])
                for capture in report["captures"]:
                    self.assertTrue(capture["valid"])
                    self.assertTrue(capture["structured"])
                    self.assertTrue(capture["round_trip_exact"])
                    self.assertTrue(capture["checksum_consistent"])
                    self.assertEqual(capture["opaque_records"], 0)
                    self.assertEqual(capture["issues"], [])
                for comparison in report["comparisons"]:
                    self.assertTrue(comparison["valid"])
                    self.assertTrue(comparison["relation_matches"])

    def test_rotary_cases_remain_blocked_without_published_files(self) -> None:
        manifest = _manifest()
        self.assertFalse(manifest["machine"]["rotary_hardware_available"])
        self.assertEqual(manifest["rotary_template"]["status"], "required")
        rotary = [
            item
            for item in _cases(manifest).values()
            if item["family"] == "rotary-candidate"
        ]
        self.assertEqual(len(rotary), 8)
        for item in rotary:
            self.assertEqual(item["fixture_status"], "blocked")
            self.assertEqual(item["export_status"], "blocked")
            self.assertIsNone(item["capture"])
            self.assertEqual(item["files"], {})
            self.assertFalse((FIXTURE / item["project"]).exists())
            self.assertFalse((FIXTURE / item["expected_rd"]).exists())

    def test_sanitizer_preserves_non_path_profile_identity(self) -> None:
        document: dict[str, Any] = {
            "DeviceList": [
                {
                    "ProfilePath": "Ruida",
                    "Settings": {
                        "LastMachineFilePath": "/Users/example/jobs",
                        "RemoteUrl": "https://example.invalid/profile",
                        "Nested": ["C:\\Users\\example\\jobs"],
                    },
                }
            ]
        }
        sanitized, redactions = sanitize_profile_document(document)
        device_list: Any = sanitized["DeviceList"]
        device = device_list[0]
        self.assertEqual(device["ProfilePath"], "Ruida")
        self.assertEqual(
            device["Settings"]["RemoteUrl"],
            "https://example.invalid/profile",
        )
        self.assertNotIn("LastMachineFilePath", device["Settings"])
        self.assertEqual(
            device["Settings"]["Nested"],
            ["<redacted-local-path>"],
        )
        self.assertEqual(len(redactions), 2)
        self.assertIn("LastMachineFilePath", document["DeviceList"][0]["Settings"])

    def test_promotion_rejects_source_destination_alias(self) -> None:
        with self.assertRaisesRegex(ValueError, "must differ"):
            promote(FIXTURE, FIXTURE)


if __name__ == "__main__":
    unittest.main()
