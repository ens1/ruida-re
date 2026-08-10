"""Tests for controlled capability-experiment analysis."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from ruida_re.codec import swizzle
from ruida_re.experiment import (
    REPORT_SCHEMA,
    SCHEMA,
    analyze,
    main,
    manifest_from_capability_fixture,
    manifest_json,
    parse_manifest,
    report_json,
)
from ruida_re.program import KnownCommand, decode

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _variant(raw_data: bytes) -> bytes:
    program = decode(raw_data)
    speed = next(
        record
        for record in program.records
        if isinstance(record, KnownCommand) and record.name == "active_speed"
    )
    speed.values["speed_mm_s"] = 11
    return program.encode(checksum_policy="recompute")


def _wrong_checksum(raw_data: bytes) -> bytes:
    program = decode(raw_data)
    checksum = next(
        record
        for record in program.records
        if isinstance(record, KnownCommand) and record.name == "file_checksum"
    )
    if program.source_checksum_basis is None:
        raise AssertionError("Fixture has no checksum basis")
    checksum.values["value"] = program.source_checksum_basis + 1
    return program.encode()


def _opaque_with_checksum() -> bytes:
    logical = b"\x80\x01"
    program = decode(swizzle(logical))
    fixture = decode(BASELINE_PATH.read_bytes())
    checksum = deepcopy(
        next(
            record
            for record in fixture.records
            if isinstance(record, KnownCommand) and record.name == "file_checksum"
        )
    )
    checksum.values["value"] = sum(logical)
    program.records.append(checksum)
    return program.encode()


def _manifest(
    before: bytes,
    after: bytes,
    *,
    relation: str = "different",
    strict: bool | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "capability": "controlled-speed",
        "provenance": {
            "application": "test-fixture",
            "version": 1,
        },
        "protocol": {
            "magic": 0x88,
            "context": "job",
            "container": "rd",
        },
        "captures": [
            {
                "id": "baseline",
                "path": "baseline.rd",
                "sha256": _digest(before),
                "controls": {
                    "speed_mm_s": 10,
                    "power_percent": 20,
                },
            },
            {
                "id": "variant",
                "path": "variant.rd",
                "sha256": _digest(after),
                "controls": {
                    "speed_mm_s": 11,
                    "power_percent": 20,
                },
            },
        ],
        "comparisons": [
            {
                "id": "speed-10-vs-11",
                "baseline": "baseline",
                "variant": "variant",
                "variable": "speed_mm_s",
                "expected_relation": relation,
            }
        ],
    }
    if strict is not None:
        result["strict"] = strict
    return result


def _write_experiment(
    directory: Path,
    before: bytes,
    after: bytes,
    *,
    relation: str = "different",
    strict: bool | None = None,
) -> Path:
    (directory / "baseline.rd").write_bytes(before)
    (directory / "variant.rd").write_bytes(after)
    path = directory / "experiment.json"
    path.write_text(
        json.dumps(
            _manifest(
                before,
                after,
                relation=relation,
                strict=strict,
            ),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


class ExperimentTest(unittest.TestCase):
    def test_analyzes_controlled_change_and_separates_checksum(self) -> None:
        baseline = BASELINE_PATH.read_bytes()
        variant = _variant(baseline)
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = _write_experiment(
                Path(temporary),
                baseline,
                variant,
            )
            report = analyze(manifest_path)

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertTrue(report["strict"])
        self.assertTrue(report["valid"])
        self.assertTrue(all(capture["valid"] for capture in report["captures"]))
        comparison = report["comparisons"][0]
        self.assertEqual(comparison["protocol_relation"], "different")
        self.assertEqual(comparison["raw_relation"], "different")
        self.assertTrue(comparison["relation_matches"])
        record_diff = comparison["record_diff"]
        self.assertEqual(len(record_diff["non_checksum"]), 1)
        self.assertEqual(len(record_diff["checksum_derived"]), 1)
        before_record = record_diff["non_checksum"][0]["before_records"][0]
        after_record = record_diff["non_checksum"][0]["after_records"][0]
        self.assertEqual(before_record["name"], "active_speed")
        self.assertEqual(after_record["name"], "active_speed")
        self.assertNotEqual(
            before_record["encoded"],
            after_record["encoded"],
        )
        byte_diff = comparison["unswizzled_diff"]
        self.assertTrue(byte_diff["non_checksum"])
        self.assertTrue(byte_diff["checksum_derived"])

    def test_identical_protocol_output_is_a_valid_observation(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        manifest = _manifest(raw_data, raw_data, relation="identical")
        captures = manifest["captures"]
        captures[0]["controls"] = {"z_per_pass_mm": 0}
        captures[1]["controls"] = {"z_per_pass_mm": 0.5}
        manifest["comparisons"][0]["variable"] = "z_per_pass_mm"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "baseline.rd").write_bytes(raw_data)
            (directory / "variant.rd").write_bytes(raw_data)
            path = directory / "experiment.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = analyze(path)

        comparison = report["comparisons"][0]
        self.assertTrue(report["valid"])
        self.assertEqual(comparison["protocol_relation"], "identical")
        self.assertEqual(comparison["raw_relation"], "identical")
        self.assertEqual(comparison["record_diff"]["non_checksum"], [])
        self.assertEqual(
            comparison["record_diff"]["checksum_derived"],
            [],
        )

    def test_expected_relation_mismatch_invalidates_report(self) -> None:
        baseline = BASELINE_PATH.read_bytes()
        variant = _variant(baseline)
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_experiment(
                Path(temporary),
                baseline,
                variant,
                relation="identical",
            )
            report = analyze(path)

        self.assertFalse(report["valid"])
        self.assertFalse(report["comparisons"][0]["relation_matches"])

    def test_observe_reports_relation_without_predicting_it(self) -> None:
        baseline = BASELINE_PATH.read_bytes()
        variant = _variant(baseline)
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_experiment(
                Path(temporary),
                baseline,
                variant,
                relation="observe",
            )
            report = analyze(path)

        comparison = report["comparisons"][0]
        self.assertTrue(report["valid"])
        self.assertEqual(comparison["expected_relation"], "observe")
        self.assertEqual(comparison["protocol_relation"], "different")
        self.assertEqual(comparison["raw_relation"], "different")
        self.assertTrue(comparison["relation_matches"])

    def test_observe_does_not_hide_an_invalid_capture(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = _write_experiment(
                directory,
                raw_data,
                raw_data,
                relation="observe",
            )
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["captures"][1]["sha256"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = analyze(path)

        comparison = report["comparisons"][0]
        self.assertTrue(comparison["relation_matches"])
        self.assertFalse(comparison["valid"])
        self.assertFalse(report["valid"])

    def test_strict_is_default_and_permissive_is_explicit(self) -> None:
        opaque = _opaque_with_checksum()
        manifest = _manifest(opaque, opaque, relation="identical")
        captures = manifest["captures"]
        captures[0]["controls"] = {"laser_2": False}
        captures[1]["controls"] = {"laser_2": True}
        manifest["comparisons"][0]["variable"] = "laser_2"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "baseline.rd").write_bytes(opaque)
            (directory / "variant.rd").write_bytes(opaque)
            path = directory / "experiment.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            strict_report = analyze(path)
            permissive_report = analyze(path, strict=False)

        self.assertFalse(strict_report["valid"])
        self.assertTrue(strict_report["strict"])
        self.assertGreater(strict_report["captures"][0]["opaque_records"], 0)
        self.assertTrue(permissive_report["valid"])
        self.assertFalse(permissive_report["strict"])
        self.assertTrue(permissive_report["captures"][0]["round_trip_exact"])

    def test_lossless_wrong_checksum_is_always_invalid(self) -> None:
        wrong_checksum = _wrong_checksum(BASELINE_PATH.read_bytes())
        self.assertEqual(decode(wrong_checksum).encode(), wrong_checksum)
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_experiment(
                Path(temporary),
                wrong_checksum,
                wrong_checksum,
                relation="identical",
            )
            strict_report = analyze(path)
            permissive_report = analyze(path, strict=False)

        for report in (strict_report, permissive_report):
            with self.subTest(strict=report["strict"]):
                self.assertFalse(report["valid"])
                for capture in report["captures"]:
                    self.assertTrue(capture["round_trip_exact"])
                    self.assertTrue(capture["checksum_required"])
                    self.assertFalse(capture["checksum_consistent"])
                    self.assertFalse(capture["valid"])
                self.assertFalse(report["comparisons"][0]["valid"])

    def test_checksum_is_required_only_for_rd_jobs(self) -> None:
        raw_data = b""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = _write_experiment(
                directory,
                raw_data,
                raw_data,
                relation="identical",
            )
            job_report = analyze(path, strict=False)
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["protocol"]["context"] = "reply"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            reply_report = analyze(path, strict=False)

        job_capture = job_report["captures"][0]
        self.assertTrue(job_capture["checksum_required"])
        self.assertIsNone(job_capture["checksum_consistent"])
        self.assertFalse(job_capture["valid"])
        self.assertFalse(job_report["valid"])
        reply_capture = reply_report["captures"][0]
        self.assertFalse(reply_capture["checksum_required"])
        self.assertIsNone(reply_capture["checksum_consistent"])
        self.assertTrue(reply_capture["valid"])
        self.assertTrue(reply_report["valid"])

    def test_hash_mismatch_invalidates_capture_and_comparison(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = _write_experiment(
                directory,
                raw_data,
                raw_data,
                relation="identical",
            )
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["captures"][1]["sha256"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = analyze(path)

        self.assertFalse(report["valid"])
        self.assertFalse(report["captures"][1]["sha256_matches"])
        self.assertFalse(report["comparisons"][0]["valid"])

    def test_missing_capture_produces_analyzable_failure(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "baseline.rd").write_bytes(raw_data)
            path = directory / "experiment.json"
            path.write_text(
                json.dumps(_manifest(raw_data, raw_data, relation="identical")),
                encoding="utf-8",
            )
            report = analyze(path)

        self.assertFalse(report["valid"])
        self.assertIn("error", report["captures"][1])
        self.assertIn("unavailable", report["comparisons"][0]["error"])

    def test_manifest_requires_only_declared_control_change(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        manifest = _manifest(raw_data, raw_data, relation="identical")
        manifest["captures"][1]["controls"]["power_percent"] = 30
        with self.assertRaisesRegex(ValueError, "change exactly"):
            parse_manifest(manifest)

    def test_manifest_rejects_unknown_relation_and_capture(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        relation = _manifest(raw_data, raw_data)
        relation["comparisons"][0]["expected_relation"] = "maybe"
        with self.assertRaisesRegex(ValueError, "expected_relation"):
            parse_manifest(relation)

        reference = _manifest(raw_data, raw_data)
        reference["comparisons"][0]["variant"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown capture"):
            parse_manifest(reference)

    def test_capture_paths_cannot_escape_manifest_directory(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        traversal = _manifest(raw_data, raw_data, relation="identical")
        traversal["captures"][0]["path"] = "../outside.rd"
        with self.assertRaisesRegex(ValueError, "must not escape"):
            parse_manifest(traversal)

        manifest = _manifest(raw_data, raw_data, relation="identical")
        manifest["captures"][0]["controls"] = {"z_mm": 0}
        manifest["captures"][1]["controls"] = {"z_mm": 1}
        manifest["comparisons"][0]["variable"] = "z_mm"
        manifest["captures"][0]["path"] = "linked.rd"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "experiment"
            directory.mkdir()
            outside = root / "outside.rd"
            outside.write_bytes(raw_data)
            (directory / "linked.rd").symlink_to(outside)
            (directory / "variant.rd").write_bytes(raw_data)
            path = directory / "experiment.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = analyze(path)

        self.assertFalse(report["valid"])
        self.assertIn("escapes", report["captures"][0]["error"])
        self.assertIn("unavailable", report["comparisons"][0]["error"])

    def test_builds_observational_manifest_from_captured_family(self) -> None:
        digest = "a" * 64
        source = {
            "schema": "fixture.example.v1",
            "provenance": {"operator": "fixture-author"},
            "reference_software": {
                "name": "LightBurn",
                "version": "2.1.03",
            },
            "device_profile": {
                "controller": "Ruida",
                "display_name": "Boss LS2040",
            },
            "scope": {"hardware_contacted": False},
            "cases": [
                {
                    "identifier": "pending",
                    "family": "diagonal-raster",
                    "export_status": "pending",
                },
                {
                    "identifier": "other-family",
                    "family": "laser-channel",
                    "export_status": "captured",
                },
                {
                    "identifier": "angle-045",
                    "family": "diagonal-raster",
                    "purpose": "isolate a diagonal scan angle",
                    "project": "angle-045.lbrn2",
                    "fixture_status": "generated",
                    "export_status": "captured",
                    "expected_rd": "captures/angle-045.rd",
                    "evidence": {
                        "input_status": "controlled-input",
                        "protocol_interpretation": "pending",
                        "hypothesis": None,
                    },
                    "profile_requirements": ["Laser2Enabled"],
                    "capture": {
                        "rd_sha256": digest,
                        "project_sha256": "b" * 64,
                        "profile_evidence": {
                            "evidence_sha256": "c" * 64,
                        },
                        "export_attestation": {
                            "machine_file_action": "save-rd",
                            "job_transmitted": False,
                        },
                    },
                    "controls": {"angle_degrees": 45, "speed": 100},
                    "files": {
                        "angle-045.lbrn2": {
                            "sha256": "b" * 64,
                            "stage": "lightburn-project",
                        },
                        "captures/angle-045.rd": {
                            "sha256": digest,
                            "size": 123,
                            "stage": "lightburn-machine-export",
                        },
                    },
                    "comparison": {
                        "baseline": "angle-000",
                        "independent_variable": "angle_degrees",
                    },
                },
                {
                    "identifier": "orphan",
                    "family": "diagonal-raster",
                    "export_status": "captured",
                    "expected_rd": "orphan.rd",
                    "controls": {"angle_degrees": 90, "speed": 100},
                    "files": {"orphan.rd": {"sha256": digest}},
                    "comparison": None,
                },
                {
                    "identifier": "angle-000",
                    "family": "diagonal-raster",
                    "export_status": "captured",
                    "expected_rd": "captures/angle-000.rd",
                    "controls": {"angle_degrees": 0, "speed": 100},
                    "files": {"captures/angle-000.rd": {"sha256": digest}},
                    "comparison": None,
                },
            ],
        }

        document = manifest_from_capability_fixture(
            source,
            "diagonal-raster",
        )

        parse_manifest(document)
        self.assertEqual(document["capability"], "diagonal-raster")
        captures = cast(list[dict[str, Any]], document["captures"])
        self.assertEqual(
            [item["id"] for item in captures],
            ["angle-000", "angle-045"],
        )
        self.assertEqual(
            document["comparisons"],
            [
                {
                    "id": "angle-000-vs-angle-045",
                    "baseline": "angle-000",
                    "variant": "angle-045",
                    "variable": "angle_degrees",
                    "expected_relation": "observe",
                }
            ],
        )
        provenance = cast(dict[str, Any], document["provenance"])
        self.assertEqual(provenance["schema"], source["schema"])
        self.assertEqual(
            provenance["provenance"],
            source["provenance"],
        )
        self.assertEqual(
            provenance["reference_software"],
            source["reference_software"],
        )
        self.assertEqual(
            provenance["device_profile"],
            source["device_profile"],
        )
        self.assertEqual(provenance["scope"], source["scope"])
        variant = next(capture for capture in captures if capture["id"] == "angle-045")
        capture_provenance = variant["provenance"]
        self.assertEqual(
            capture_provenance["purpose"],
            "isolate a diagonal scan angle",
        )
        self.assertEqual(
            capture_provenance["capture"],
            source["cases"][2]["capture"],
        )
        self.assertEqual(
            capture_provenance["evidence"],
            source["cases"][2]["evidence"],
        )
        self.assertEqual(
            capture_provenance["profile_requirements"],
            ["Laser2Enabled"],
        )
        self.assertEqual(
            capture_provenance["files"],
            {
                "angle-045.lbrn2": {
                    "sha256": "b" * 64,
                    "stage": "lightburn-project",
                }
            },
        )
        self.assertNotIn("expected_rd", capture_provenance)
        self.assertEqual(
            capture_provenance["rd_file_metadata"],
            {
                "size": 123,
                "stage": "lightburn-machine-export",
            },
        )

        reordered = dict(source)
        reordered["cases"] = list(reversed(source["cases"]))
        self.assertEqual(
            json.dumps(document, allow_nan=False, sort_keys=True),
            json.dumps(
                manifest_from_capability_fixture(
                    reordered,
                    "diagonal-raster",
                ),
                allow_nan=False,
                sort_keys=True,
            ),
        )

    def test_fixture_conversion_rejects_unsafe_capture_path(self) -> None:
        source = {
            "schema": "fixture.example.v1",
            "cases": [
                {
                    "identifier": "baseline",
                    "family": "z-axis-candidate",
                    "export_status": "captured",
                    "expected_rd": "baseline.rd",
                    "controls": {"z_mm": 0},
                    "files": {"baseline.rd": {"sha256": "a" * 64}},
                    "comparison": None,
                },
                {
                    "identifier": "variant",
                    "family": "z-axis-candidate",
                    "export_status": "captured",
                    "expected_rd": "../variant.rd",
                    "controls": {"z_mm": 1},
                    "files": {"../variant.rd": {"sha256": "b" * 64}},
                    "comparison": {
                        "baseline": "baseline",
                        "independent_variable": "z_mm",
                    },
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, "must not escape"):
            manifest_from_capability_fixture(source, "z-axis-candidate")

    def test_fixture_conversion_requires_captured_comparison(self) -> None:
        source = {
            "schema": "fixture.example.v1",
            "cases": [
                {
                    "identifier": "baseline",
                    "family": "laser-channel",
                    "export_status": "captured",
                    "expected_rd": "baseline.rd",
                    "controls": {"laser_2": False},
                    "files": {"baseline.rd": {"sha256": "a" * 64}},
                    "comparison": None,
                },
                {
                    "identifier": "variant",
                    "family": "laser-channel",
                    "export_status": "pending",
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, "no captured comparisons"):
            manifest_from_capability_fixture(source, "laser-channel")

    def test_report_json_and_cli_output_are_deterministic(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        manifest = _manifest(raw_data, raw_data, relation="identical")
        manifest["captures"][0]["controls"] = {"z_mm": 0}
        manifest["captures"][1]["controls"] = {"z_mm": 1}
        manifest["comparisons"][0]["variable"] = "z_mm"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "baseline.rd").write_bytes(raw_data)
            (directory / "variant.rd").write_bytes(raw_data)
            path = directory / "experiment.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            expected = report_json(analyze(path))
            output = directory / "report.json"
            with patch(
                "sys.argv",
                [
                    "ruida-experiment",
                    str(path),
                    "--output",
                    str(output),
                ],
            ):
                main()
            self.assertEqual(output.read_text(encoding="utf-8"), expected)
            parsed = json.loads(expected)
            self.assertEqual(parsed["schema"], REPORT_SCHEMA)

    def test_cli_error_is_json_and_cannot_overwrite_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "experiment.json"
            original = b"{}"
            path.write_bytes(original)
            stdout = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        str(path),
                        "--output",
                        str(path),
                        "--force",
                    ],
                ),
                redirect_stdout(stdout),
                self.assertRaises(SystemExit) as raised,
            ):
                main()
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(path.read_bytes(), original)
            failure = json.loads(stdout.getvalue())
            self.assertEqual(failure["schema"], REPORT_SCHEMA)
            self.assertFalse(failure["valid"])

    def test_cli_cannot_overwrite_capture_or_evidence_alias(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()

        def assert_rejected(manifest: Path, output: Path) -> None:
            stdout = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        str(manifest),
                        "--output",
                        str(output),
                        "--force",
                    ],
                ),
                redirect_stdout(stdout),
                self.assertRaises(SystemExit) as raised,
            ):
                main()
            self.assertEqual(raised.exception.code, 2)
            failure = json.loads(stdout.getvalue())
            self.assertFalse(failure["valid"])
            self.assertIn("overwrite experiment", failure["error"])

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = _write_experiment(
                directory,
                raw_data,
                raw_data,
                relation="identical",
            )
            baseline = directory / "baseline.rd"
            baseline_before = baseline.read_bytes()
            assert_rejected(manifest, baseline)
            self.assertEqual(baseline.read_bytes(), baseline_before)

            capture_alias = directory / "capture-report.json"
            capture_alias.symlink_to(baseline.name)
            assert_rejected(manifest, capture_alias)
            self.assertTrue(capture_alias.is_symlink())
            self.assertEqual(baseline.read_bytes(), baseline_before)

            manifest_alias = directory / "manifest-report.json"
            manifest_alias.symlink_to(manifest.name)
            assert_rejected(manifest, manifest_alias)
            self.assertTrue(manifest_alias.is_symlink())

            missing_capture = directory / "variant.rd"
            missing_capture.unlink()
            assert_rejected(manifest, missing_capture)
            self.assertFalse(missing_capture.exists())

    def test_cli_derives_family_without_analysis_or_side_effects(self) -> None:
        raw_data = BASELINE_PATH.read_bytes()
        digest = _digest(raw_data)
        source = {
            "schema": "fixture.example.v1",
            "reference_software": {
                "name": "LightBurn",
                "version": "2.1.03",
            },
            "device_profile": {"controller": "Ruida"},
            "scope": {"hardware_contacted": False},
            "cases": [
                {
                    "identifier": "angle-000",
                    "family": "diagonal-raster",
                    "purpose": "horizontal baseline",
                    "project": "angle-000.lbrn2",
                    "fixture_status": "generated",
                    "export_status": "captured",
                    "expected_rd": "angle-000.rd",
                    "evidence": {
                        "input_status": "controlled-input",
                        "hypothesis": None,
                    },
                    "profile_requirements": [],
                    "capture": {
                        "rd_sha256": digest,
                        "project_sha256": "a" * 64,
                        "profile_evidence": {
                            "filename": "profile-evidence.json",
                            "evidence_sha256": "c" * 64,
                        },
                        "export_attestation": {
                            "machine_file_action": "save-rd",
                            "job_transmitted": False,
                        },
                    },
                    "controls": {"angle_degrees": 0, "speed": 100},
                    "files": {
                        "angle-000.lbrn2": {"sha256": "a" * 64},
                        "angle-000.rd": {"sha256": digest},
                    },
                    "comparison": None,
                },
                {
                    "identifier": "angle-045",
                    "family": "diagonal-raster",
                    "purpose": "diagonal variant",
                    "project": "angle-045.lbrn2",
                    "fixture_status": "generated",
                    "export_status": "captured",
                    "expected_rd": "angle-045.rd",
                    "evidence": {
                        "input_status": "controlled-input",
                        "hypothesis": None,
                    },
                    "profile_requirements": [],
                    "capture": {
                        "rd_sha256": digest,
                        "project_sha256": "b" * 64,
                        "profile_evidence": {
                            "filename": "profile-evidence.json",
                            "evidence_sha256": "c" * 64,
                        },
                        "export_attestation": {
                            "machine_file_action": "save-rd",
                            "job_transmitted": False,
                        },
                    },
                    "controls": {"angle_degrees": 45, "speed": 100},
                    "files": {
                        "angle-045.lbrn2": {"sha256": "b" * 64},
                        "angle-045.rd": {"sha256": digest},
                    },
                    "comparison": {
                        "baseline": "angle-000",
                        "independent_variable": "angle_degrees",
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            capability_path = directory / "capabilities.json"
            capability_path.write_text(
                json.dumps(source),
                encoding="utf-8",
            )
            (directory / "angle-000.rd").write_bytes(raw_data)
            (directory / "angle-045.rd").write_bytes(raw_data)
            (directory / "angle-000.lbrn2").write_text(
                "baseline project",
                encoding="utf-8",
            )
            (directory / "angle-045.lbrn2").write_text(
                "variant project",
                encoding="utf-8",
            )
            profile_evidence = directory / "profile-evidence.json"
            profile_evidence.write_text("{}", encoding="utf-8")
            stdout = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        "derive",
                        str(capability_path),
                        "diagonal-raster",
                    ],
                ),
                patch("ruida_re.experiment.analyze") as analyze_mock,
                redirect_stdout(stdout),
            ):
                main()

            output = directory / "diagonal-raster.experiment.json"
            expected = manifest_from_capability_fixture(
                source,
                "diagonal-raster",
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"), manifest_json(expected)
            )
            self.assertEqual(stdout.getvalue(), f"{output}\n")
            analyze_mock.assert_not_called()
            parsed = json.loads(output.read_text(encoding="utf-8"))
            manifest = parse_manifest(parsed)
            self.assertEqual(
                [capture.path for capture in manifest.captures],
                ["angle-000.rd", "angle-045.rd"],
            )
            report = analyze(output)
            self.assertEqual(
                report["captures"][0]["provenance"]["capture"]["profile_evidence"][
                    "filename"
                ],
                "profile-evidence.json",
            )
            original = output.read_bytes()
            stderr = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        "derive",
                        str(capability_path),
                        "diagonal-raster",
                    ],
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                main()
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(output.read_bytes(), original)

            evidence_before = profile_evidence.read_bytes()
            evidence_alias = directory / "evidence-output.json"
            evidence_alias.symlink_to(profile_evidence.name)
            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        "derive",
                        str(capability_path),
                        "diagonal-raster",
                        "--output",
                        evidence_alias.name,
                        "--force",
                    ],
                ),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as evidence_raised,
            ):
                main()
            self.assertEqual(evidence_raised.exception.code, 2)
            self.assertTrue(evidence_alias.is_symlink())
            self.assertEqual(profile_evidence.read_bytes(), evidence_before)
            self.assertIn("FileExistsError", stderr.getvalue())

            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        "derive",
                        str(capability_path),
                        "diagonal-raster",
                        "--force",
                    ],
                ),
                redirect_stdout(io.StringIO()),
            ):
                main()
            self.assertEqual(output.read_bytes(), original)

    def test_cli_derive_rejects_output_outside_capability_directory(self) -> None:
        source = {
            "schema": "fixture.example.v1",
            "cases": [
                {
                    "identifier": "baseline",
                    "family": "laser-channel",
                    "export_status": "captured",
                    "expected_rd": "baseline.rd",
                    "controls": {"laser_2": False},
                    "files": {"baseline.rd": {"sha256": "a" * 64}},
                    "comparison": None,
                },
                {
                    "identifier": "variant",
                    "family": "laser-channel",
                    "export_status": "captured",
                    "expected_rd": "variant.rd",
                    "controls": {"laser_2": True},
                    "files": {"variant.rd": {"sha256": "b" * 64}},
                    "comparison": {
                        "baseline": "baseline",
                        "independent_variable": "laser_2",
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "fixtures"
            directory.mkdir()
            capability_path = directory / "capabilities.json"
            capability_path.write_text(json.dumps(source), encoding="utf-8")
            outside = root / "outside.json"
            with (
                patch(
                    "sys.argv",
                    [
                        "ruida-experiment",
                        "derive",
                        str(capability_path),
                        "laser-channel",
                        "--output",
                        str(outside),
                        "--force",
                    ],
                ),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main()
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(outside.exists())

    def test_cli_help_lists_explicit_commands(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.argv", ["ruida-experiment", "--help"]),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as raised,
        ):
            main()
        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        self.assertIn("analyze", help_text)
        self.assertIn("derive", help_text)
        self.assertIn("Legacy analyze syntax", help_text)


if __name__ == "__main__":
    unittest.main()
