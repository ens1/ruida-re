"""Tests for controlled offline LightBurn device profiles."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from typing import Any

from ruida_re.lightburn_profile import (
    MANIFEST_NAME,
    VARIANTS,
    generate,
    main,
    snapshot,
)


def _source_document() -> dict[str, Any]:
    return {
        "ExportVersion": 17,
        "UnknownTopLevel": {"items": [1, "two", {"three": 3}]},
        "DeviceList": [
            {
                "DisplayName": "Boss LS2040",
                "GUID": "sourceGuid",
                "Name": "Ruida",
                "Type": "Serial",
                "Width": 1016.0,
                "Height": 508.0,
                "UnknownDeviceField": [False, None, 2.5],
                "Settings": {
                    "EnableZ": False,
                    "Laser2Enabled": False,
                    "ExistingUnknownSetting": {"nested": "preserved"},
                },
            }
        ],
    }


def _write_source(path: Path, document: object | None = None) -> Path:
    if document is None:
        document = _source_document()
    path.write_text(
        json.dumps(document, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: object) -> bytes:
    content = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (content + "\n").encode("utf-8")


def _prefs_document() -> dict[str, Any]:
    document = _source_document()
    ruida = document["DeviceList"][0]
    document["DeviceList"] = [
        {
            "DisplayName": "Not selected",
            "GUID": "grblGuid",
            "Name": "GRBL",
            "Type": "Serial",
        },
        ruida,
        {
            "DisplayName": "Also not selected",
            "GUID": "marlinGuid",
            "Name": "Marlin",
            "Type": "Serial",
        },
    ]
    return document


class LightBurnProfileTest(unittest.TestCase):
    def test_snapshot_extracts_only_the_ruida_device_canonically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = _prefs_document()
            prefs = _write_source(root / "LightBurnPrefs.json", document)
            original = prefs.read_bytes()
            output = root / "boss.lbdev"

            provenance = snapshot(prefs, output)

            selected = document["DeviceList"][1]
            expected_output = {"DeviceList": [selected]}
            self.assertEqual(output.read_bytes(), _canonical(expected_output))
            self.assertEqual(_load(output), expected_output)
            self.assertEqual(prefs.read_bytes(), original)
            self.assertEqual(provenance["device_list_index"], 1)
            self.assertEqual(provenance["profile_type"], "Serial")
            self.assertEqual(
                provenance["controller_identity"],
                {
                    "field": "DeviceList[0].Name",
                    "value": "Ruida",
                },
            )

    def test_snapshot_provenance_hashes_raw_selected_and_output_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = _prefs_document()
            prefs = _write_source(root / "LightBurnPrefs.json", document)
            output = root / "boss.lbdev"

            provenance = snapshot(prefs, output)

            selected = document["DeviceList"][1]
            self.assertEqual(
                provenance["raw_prefs_sha256"],
                hashlib.sha256(prefs.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                provenance["selected_device_sha256"],
                hashlib.sha256(_canonical(selected)).hexdigest(),
            )
            self.assertEqual(
                provenance["output_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                provenance["output_size"],
                len(output.read_bytes()),
            )
            self.assertEqual(
                provenance["prefs_path"],
                str(prefs.resolve()),
            )
            self.assertEqual(
                provenance["output_path"],
                str(output.resolve()),
            )

    def test_snapshot_requires_exactly_one_ruida_device(self) -> None:
        for count in (0, 2):
            with (
                self.subTest(count=count),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                document = _prefs_document()
                ruida = document["DeviceList"][1]
                document["DeviceList"] = [deepcopy(ruida) for _ in range(count)]
                prefs = _write_source(root / "prefs.json", document)
                with self.assertRaisesRegex(
                    ValueError,
                    f"exactly one Ruida.*found {count}",
                ):
                    snapshot(prefs, root / "boss.lbdev")

    def test_snapshot_requires_selected_ruida_type_to_be_a_string(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = _prefs_document()
            document["DeviceList"][1]["Type"] = None
            prefs = _write_source(root / "prefs.json", document)

            with self.assertRaisesRegex(TypeError, "Type must be a string"):
                snapshot(prefs, root / "boss.lbdev")

    def test_snapshot_requires_lbdev_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefs = _write_source(
                root / "prefs-without-required-suffix",
                _prefs_document(),
            )

            with self.assertRaisesRegex(ValueError, "end in .lbdev"):
                snapshot(prefs, root / "boss.json")

    def test_snapshot_no_clobber_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefs = _write_source(
                root / "prefs.json",
                _prefs_document(),
            )
            output = root / "boss.lbdev"
            output.write_text("existing", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                snapshot(prefs, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing")

            provenance = snapshot(prefs, output, force=True)
            self.assertEqual(
                provenance["output_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )

    def test_snapshot_rejects_resolved_and_hard_link_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefs = _write_source(
                root / "prefs.lbdev",
                _prefs_document(),
            )
            original = prefs.read_bytes()
            symlink = root / "symlink.lbdev"
            symlink.symlink_to(prefs)
            hard_link = root / "hard-link.lbdev"
            hard_link.hardlink_to(prefs)

            for output in (prefs, symlink, hard_link):
                with (
                    self.subTest(output=output.name),
                    self.assertRaisesRegex(ValueError, "must be different"),
                ):
                    snapshot(prefs, output, force=True)
            self.assertEqual(prefs.read_bytes(), original)

    def test_snapshot_cli_emits_machine_readable_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefs = _write_source(
                root / "prefs.json",
                _prefs_document(),
            )
            output = root / "boss.lbdev"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["snapshot", str(prefs), str(output)])

            provenance = json.loads(stdout.getvalue())
            self.assertEqual(
                provenance["output_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )

            stderr = io.StringIO()
            with (
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as error,
            ):
                main(["snapshot", str(prefs), str(output)])
            self.assertEqual(error.exception.code, 1)
            self.assertIn("error:", stderr.getvalue())

    def test_explicit_generate_cli_preserves_generate_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")
            output = root / "matrix"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["generate", str(source), str(output)])

            self.assertEqual(
                stdout.getvalue().strip(),
                str(output / MANIFEST_NAME),
            )

    def test_generate_makes_one_controlled_settings_change_per_clone(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")
            original_bytes = source.read_bytes()
            output = root / "matrix"

            manifest_path = generate(source, output)

            self.assertEqual(manifest_path, output / MANIFEST_NAME)
            self.assertEqual(source.read_bytes(), original_bytes)
            source_document = _load(source)
            source_device = source_document["DeviceList"][0]
            generated_guids = set()
            for variant in VARIANTS:
                with self.subTest(variant=variant.identifier):
                    clone = _load(output / variant.filename)
                    clone_device = clone["DeviceList"][0]
                    self.assertEqual(
                        clone["UnknownTopLevel"],
                        source_document["UnknownTopLevel"],
                    )
                    expected_device = deepcopy(source_device)
                    expected_device["DisplayName"] = clone_device["DisplayName"]
                    expected_device["GUID"] = clone_device["GUID"]
                    expected_device["Settings"][variant.setting] = True
                    self.assertEqual(clone_device, expected_device)
                    self.assertTrue(
                        clone_device["DisplayName"].startswith(
                            "RESEARCH ONLY - Boss LS2040 - "
                        )
                    )
                    self.assertNotEqual(
                        clone_device["GUID"],
                        source_device["GUID"],
                    )
                    generated_guids.add(clone_device["GUID"])
            self.assertEqual(len(generated_guids), len(VARIANTS))

    def test_manifest_records_source_and_variant_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")
            output = root / "matrix"

            manifest = _load(generate(source, output))

            self.assertEqual(
                manifest["source"]["source_sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                manifest["source"]["controller_identity"],
                {
                    "field": "DeviceList[0].Name",
                    "value": "Ruida",
                },
            )
            self.assertEqual(manifest["source"]["profile_type"], "Serial")
            entries = {item["identifier"]: item for item in manifest["variants"]}
            self.assertEqual(set(entries), {item.identifier for item in VARIANTS})
            for variant in VARIANTS:
                with self.subTest(variant=variant.identifier):
                    entry = entries[variant.identifier]
                    content = (output / variant.filename).read_bytes()
                    self.assertEqual(entry["changed_key"], variant.setting)
                    self.assertEqual(entry["target_value"], True)
                    self.assertEqual(entry["size"], len(content))
                    self.assertEqual(
                        entry["variant_sha256"],
                        hashlib.sha256(content).hexdigest(),
                    )
                    self.assertEqual(len(entry["derivation_sha256"]), 64)

    def test_matrix_includes_every_capability_prerequisite(self) -> None:
        self.assertEqual(
            {variant.setting for variant in VARIANTS},
            {
                "EnableZ",
                "Laser2Enabled",
                "Laser1IsRFTube",
                "Laser1IsFiber",
                "SaveRotaryConfig",
            },
        )

    def test_output_is_deterministic_across_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")
            first = root / "first"
            second = root / "second"

            generate(source, first)
            generate(source, second)

            filenames = [item.filename for item in VARIANTS]
            filenames.append(MANIFEST_NAME)
            for filename in filenames:
                with self.subTest(filename=filename):
                    self.assertEqual(
                        (first / filename).read_bytes(),
                        (second / filename).read_bytes(),
                    )

    def test_no_clobber_is_default_and_force_replaces_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")
            output = root / "matrix"
            generate(source, output)
            target = output / VARIANTS[0].filename
            target.write_text("changed", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                generate(source, output)
            self.assertEqual(target.read_text(encoding="utf-8"), "changed")

            generate(source, output, force=True)
            self.assertNotEqual(target.read_text(encoding="utf-8"), "changed")

    def test_requires_exactly_one_device(self) -> None:
        for count in (0, 2):
            with (
                self.subTest(count=count),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                document = _source_document()
                device = document["DeviceList"][0]
                document["DeviceList"] = [deepcopy(device) for _ in range(count)]
                source = _write_source(root / "boss.lbdev", document)
                with self.assertRaisesRegex(ValueError, "one DeviceList"):
                    generate(source, root / "matrix")

    def test_accepts_ruida_controller_with_serial_profile_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")

            manifest = _load(generate(source, root / "matrix"))

            self.assertEqual(
                manifest["source"]["controller_identity"]["value"],
                "Ruida",
            )
            self.assertEqual(manifest["source"]["profile_type"], "Serial")

    def test_rejects_non_ruida_without_display_name_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = _source_document()
            device = document["DeviceList"][0]
            device["DisplayName"] = "Ruida 644XS"
            device["Name"] = "GRBL"
            device["Type"] = "Serial"
            source = _write_source(root / "grbl.lbdev", document)

            with self.assertRaisesRegex(
                ValueError,
                "controller Name must be exactly 'Ruida'",
            ):
                generate(source, root / "matrix")

    def test_rejects_missing_controller_name_as_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = _source_document()
            del document["DeviceList"][0]["Name"]
            source = _write_source(root / "ambiguous.lbdev", document)

            with self.assertRaisesRegex(TypeError, "Name must be a string"):
                generate(source, root / "matrix")

    def test_rejects_non_boolean_and_already_enabled_controls(self) -> None:
        cases = (
            ("EnableZ", "yes", TypeError, "must be a boolean"),
            ("Laser1IsRFTube", True, ValueError, "already true"),
        )
        for key, value, exception, message in cases:
            with (
                self.subTest(key=key),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                document = _source_document()
                document["DeviceList"][0]["Settings"][key] = value
                source = _write_source(root / "boss.lbdev", document)
                with self.assertRaisesRegex(exception, message):
                    generate(source, root / "matrix")

    def test_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "boss.lbdev"
            source.write_text(
                '{"DeviceList": [], "DeviceList": []}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                generate(source, root / "matrix")

    def test_refuses_to_overwrite_the_source_even_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / VARIANTS[0].filename)
            with self.assertRaisesRegex(ValueError, "overwrite input"):
                generate(source, root, force=True)

    def test_cli_prints_manifest_and_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_source(root / "boss.lbdev")
            output = root / "matrix"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                main([str(source), str(output)])
            self.assertEqual(stdout.getvalue().strip(), str(output / MANIFEST_NAME))

            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main([str(source), str(output)])
            self.assertEqual(raised.exception.code, 1)
            self.assertIn("error:", stderr.getvalue())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                main([str(source), str(output), "--force"])
            self.assertEqual(stdout.getvalue().strip(), str(output / MANIFEST_NAME))


if __name__ == "__main__":
    unittest.main()
