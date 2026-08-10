"""Tests for offline advanced-capability LightBurn fixtures."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

from ruida_re.capability_fixture import (
    CASES,
    MANIFEST_NAME,
    CapabilityCase,
    RasterProject,
    RotaryProject,
    VectorProject,
    build_project,
    controlled_differences,
    generate,
    record,
    validate_cases,
)
from ruida_re.raster_fixture import encode_grayscale_png

RASTER_SETTING_FIELDS = {
    "index",
    "name",
    "minPower",
    "maxPower",
    "minPower2",
    "maxPower2",
    "speed",
    "enableLaser1",
    "enableLaser2",
    "priority",
    "doOutput",
    "runBlower",
    "autoBlower",
    "numPasses",
    "zPerPass",
    "zOffset",
    "scanOpt",
    "bidir",
    "crossHatch",
    "overscan",
    "overscanPercent",
    "interval",
    "angle",
    "negative",
    "passThrough",
    "ditherMode",
    "cleanupPass",
    "dpi",
    "linkDPItoInterval",
}

VECTOR_SETTING_FIELDS = {
    "index",
    "name",
    "minPower",
    "maxPower",
    "minPower2",
    "maxPower2",
    "speed",
    "enableLaser1",
    "enableLaser2",
    "startDelay",
    "endDelay",
    "throughPower",
    "throughPower2",
    "enableCutThroughStart",
    "enableCutThroughEnd",
    "frequency",
    "overrideFrequency",
    "fiberPulseWidth",
    "priority",
    "doOutput",
    "runBlower",
    "autoBlower",
    "numPasses",
    "dotMode",
    "dotTime",
    "dotSpacing",
}


def _by_id() -> dict[str, CapabilityCase]:
    return {case.identifier: case for case in CASES}


def _setting_values(project: ET.Element) -> dict[str, str]:
    setting = project.find("CutSetting_Img")
    if setting is None:
        setting = project.find("CutSetting")
    if setting is None:
        raise AssertionError("Missing LightBurn cut setting")
    return {child.tag: child.get("Value", "") for child in setting}


def _write_rotary_template(
    path: Path,
    *,
    config_count: int = 1,
    omit_field: str | None = None,
) -> Path:
    project = ET.Element(
        "LightBurnProject",
        AppVersion="2.1.03",
        DeviceName="Ruida 644XS",
        FormatVersion="1",
        UnknownProjectAttribute="preserved",
    )
    for index in range(config_count):
        attributes = {
            "Enabled": "False",
            "Axis": "opaque-axis-token",
            "Diameter": "32.5",
            "Convex": "True",
            "StepsPerRotation": "6400",
            "MirrorOutput": "False",
            "IsChuck": "False",
            "ObjectDiameter": "60",
            "UnknownRotaryAttribute": f"preserved-{index}",
        }
        if omit_field is not None:
            attributes.pop(omit_field)
        config = ET.SubElement(
            project,
            "GantryRotaryConfig",
            attrib=attributes,
        )
        ET.SubElement(
            config,
            "UnknownRotaryChild",
            Value="preserved",
        )
    unknown = ET.SubElement(project, "UnknownProjectChild", Kind="preserved")
    unknown.text = "unknown content"
    ET.ElementTree(project).write(
        path,
        encoding="utf-8",
        xml_declaration=True,
    )
    return path


def _write_profile_evidence(
    path: Path,
    **setting_overrides: bool,
) -> Path:
    settings = {
        "EnableZ": True,
        "Laser1IsFiber": True,
        "Laser1IsRFTube": True,
        "Laser2Enabled": True,
        "SaveRotaryConfig": True,
        **setting_overrides,
    }
    path.write_text(
        json.dumps(
            {
                "DeviceList": [
                    {
                        "DisplayName": "Ruida 644XS research profile",
                        "GUID": "researchProfile",
                        "Name": "Ruida",
                        "Type": "Serial",
                        "Settings": settings,
                    }
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _rotary_config(project: ET.Element) -> ET.Element:
    configs = list(project.iter("GantryRotaryConfig"))
    if len(configs) != 1:
        raise AssertionError("Expected one GantryRotaryConfig")
    return configs[0]


def _as_raster(project: object) -> RasterProject:
    if not isinstance(project, RasterProject):
        raise TypeError("Expected RasterProject")
    return project


def _as_vector(project: object) -> VectorProject:
    if not isinstance(project, VectorProject):
        raise TypeError("Expected VectorProject")
    return project


class CapabilityFixtureTest(unittest.TestCase):
    def test_case_graph_has_only_declared_one_variable_changes(self) -> None:
        validate_cases()
        by_id = _by_id()
        for case in CASES:
            if case.baseline is None:
                continue
            with self.subTest(case=case.identifier):
                self.assertEqual(
                    controlled_differences(by_id[case.baseline], case),
                    {case.independent_variable},
                )

    def test_invalid_comparison_is_rejected(self) -> None:
        invalid = replace(
            CASES[1],
            independent_variable="bidirectional",
        )
        with self.assertRaisesRegex(ValueError, "Uncontrolled comparison"):
            validate_cases((CASES[0], invalid))

    def test_uncontrolled_profile_requirement_is_rejected(self) -> None:
        baseline = CASES[0]
        invalid = replace(
            CASES[1],
            profile_requirements=("EnableZ",),
        )
        with self.assertRaisesRegex(
            ValueError,
            "Uncontrolled profile requirements",
        ):
            validate_cases((baseline, invalid))

    def test_invalid_power_scale_sequence_is_rejected(self) -> None:
        invalid_project = replace(
            VectorProject(),
            power_scale_sequence=(101,),
        )
        invalid = CapabilityCase(
            "invalid-power-scale",
            "dynamic-vector-power",
            "reject invalid PowerScale percentages",
            invalid_project,
        )
        with self.assertRaisesRegex(ValueError, "PowerScale outside"):
            validate_cases((invalid,))

    def test_diagonal_raster_matrix_is_explicit(self) -> None:
        by_id = _by_id()
        angle_45_uni = _as_raster(by_id["c002-raster-angle-045-uni"].project)
        angle_45_bi = _as_raster(by_id["c003-raster-angle-045-bi"].project)
        angle_135_uni = _as_raster(by_id["c004-raster-angle-135-uni"].project)
        cross_hatch = _as_raster(by_id["c005-raster-angle-045-cross-hatch"].project)
        self.assertEqual(angle_45_uni.angle_degrees, 45)
        self.assertFalse(angle_45_uni.bidirectional)
        self.assertEqual(angle_45_bi.angle_degrees, 45)
        self.assertTrue(angle_45_bi.bidirectional)
        self.assertEqual(angle_135_uni.angle_degrees, 135)
        self.assertFalse(angle_135_uni.bidirectional)
        self.assertEqual(cross_hatch.angle_degrees, 45)
        self.assertTrue(cross_hatch.cross_hatch)

    def test_laser_channel_matrix_separates_enable_and_power(self) -> None:
        by_id = _by_id()
        laser1 = _as_vector(by_id["c006-laser1-only-static"].project)
        both = _as_vector(by_id["c007-lasers-1-and-2-static"].project)
        laser2 = _as_vector(by_id["c008-laser2-only-static"].project)
        laser1_power = _as_vector(by_id["c009-lasers-both-laser1-power-25"].project)
        laser2_power = _as_vector(by_id["c010-lasers-both-laser2-power-45"].project)
        self.assertEqual(
            (laser1.enable_laser1, laser1.enable_laser2),
            (True, False),
        )
        self.assertEqual(
            (both.enable_laser1, both.enable_laser2),
            (True, True),
        )
        self.assertEqual(
            (laser2.enable_laser1, laser2.enable_laser2),
            (False, True),
        )
        self.assertEqual(
            (
                laser1_power.laser1_power_percent,
                laser1_power.laser2_power_percent,
            ),
            (25, 40),
        )
        self.assertEqual(
            (
                laser2_power.laser1_power_percent,
                laser2_power.laser2_power_percent,
            ),
            (20, 45),
        )

    def test_dynamic_power_uses_separate_touching_shapes(self) -> None:
        by_id = _by_id()
        identifiers = (
            "c015-power-scale-omitted",
            "c016-power-scale-all-000",
            "c017-power-scale-all-050",
            "c018-power-scale-all-100",
            "c019-power-scale-sequence-000-050-100",
            "c020-power-scale-sequence-100-050-000",
            "c021-power-scale-sequence-repeated-050",
        )
        expected_scales = (
            (None, None, None),
            (0, 0, 0),
            (50, 50, 50),
            (100, 100, 100),
            (0, 50, 100),
            (100, 50, 0),
            (0, 50, 50),
        )
        for identifier, scales in zip(
            identifiers,
            expected_scales,
            strict=True,
        ):
            with self.subTest(case=identifier):
                case = by_id[identifier]
                project = build_project(case)
                settings = _setting_values(project)
                self.assertEqual(float(settings["minPower"]), 10)
                self.assertEqual(float(settings["maxPower"]), 70)
                shapes = project.findall("Shape")
                self.assertEqual(len(shapes), len(scales))
                self.assertEqual(
                    tuple(
                        None
                        if shape.get("PowerScale") is None
                        else float(shape.get("PowerScale", ""))
                        for shape in shapes
                    ),
                    scales,
                )
                self.assertEqual(
                    [
                        float(shape.findtext("XForm", "").split()[-2])
                        for shape in shapes
                    ],
                    [20 + index * 10 for index in range(len(scales))],
                )
                self.assertTrue(
                    all(
                        shape.findtext("VertList") == "V 0 0 V 10 0" for shape in shapes
                    )
                )

    def test_delay_cut_through_and_dot_controls_are_exact(self) -> None:
        by_id = _by_id()
        expected = {
            "c023-start-delay-100": {"startDelay": "100"},
            "c024-start-delay-200": {"startDelay": "200"},
            "c025-end-delay-100": {"endDelay": "100"},
            "c026-end-delay-200": {"endDelay": "200"},
            "c028-cut-through-start-enabled": {
                "enableCutThroughStart": "1",
                "throughPower": "25",
                "throughPower2": "45",
            },
            "c029-cut-through-end-enabled": {
                "enableCutThroughEnd": "1",
                "throughPower": "25",
                "throughPower2": "45",
            },
            "c031-cut-through-laser1-power-35": {
                "throughPower": "35",
            },
            "c032-cut-through-laser2-power-55": {
                "throughPower2": "55",
            },
            "c034-dot-mode-enabled": {
                "dotMode": "1",
                "dotTime": "100",
                "dotSpacing": "1",
            },
            "c035-dot-time-200": {"dotTime": "200"},
            "c036-dot-spacing-2": {"dotSpacing": "2"},
        }
        for identifier, values in expected.items():
            with self.subTest(case=identifier):
                settings = _setting_values(build_project(by_id[identifier]))
                for field, value in values.items():
                    self.assertEqual(settings[field], value)

    def test_frequency_and_pulse_width_require_profile_modes(self) -> None:
        by_id = _by_id()
        frequency_cases = (
            "c037-frequency-override-disabled-20000",
            "c038-frequency-override-enabled-20000",
            "c039-frequency-override-enabled-10000",
        )
        for identifier in frequency_cases:
            case = by_id[identifier]
            self.assertEqual(
                case.profile_requirements,
                ("Laser1IsRFTube",),
            )
            self.assertEqual(case.evidence_status, "controlled-input")
        disabled = _setting_values(build_project(by_id[frequency_cases[0]]))
        enabled = _setting_values(build_project(by_id[frequency_cases[1]]))
        ten_khz = _setting_values(build_project(by_id[frequency_cases[2]]))
        self.assertEqual(disabled["overrideFrequency"], "0")
        self.assertEqual(enabled["overrideFrequency"], "1")
        self.assertEqual(enabled["frequency"], "20000")
        self.assertEqual(ten_khz["frequency"], "10000")

        pulse_cases = (
            "c040-fiber-pulse-width-zero",
            "c041-fiber-pulse-width-100",
            "c042-fiber-pulse-width-200",
        )
        for identifier in pulse_cases:
            case = by_id[identifier]
            self.assertEqual(case.evidence_status, "hypothesis")
            self.assertIsNotNone(case.hypothesis)
            self.assertEqual(
                case.profile_requirements,
                ("Laser1IsFiber",),
            )
        self.assertEqual(
            _setting_values(build_project(by_id[pulse_cases[1]]))["fiberPulseWidth"],
            "100",
        )

    def test_laser_channel_cases_require_laser2_profile_support(self) -> None:
        cases = [case for case in CASES if case.family == "laser-channel"]
        self.assertTrue(cases)
        for case in cases:
            self.assertEqual(
                case.profile_requirements,
                ("Laser2Enabled",),
            )

    def test_z_candidates_are_labelled_as_pending_hypotheses(self) -> None:
        z_cases = [case for case in CASES if case.family == "z-axis-candidate"]
        self.assertEqual(len(z_cases), 6)
        for case in z_cases:
            with self.subTest(case=case.identifier):
                self.assertEqual(case.evidence_status, "hypothesis")
                self.assertIsNotNone(case.hypothesis)
                self.assertEqual(case.profile_requirements, ("EnableZ",))
        by_id = _by_id()
        positive = _as_raster(by_id["c012-z-per-pass-positive-05"].project)
        negative = _as_raster(by_id["c013-z-per-pass-negative-05"].project)
        height = _as_raster(by_id["c014-material-height-positive-1"].project)
        offset_positive = _as_raster(by_id["c043-z-offset-positive-1"].project)
        offset_negative = _as_raster(by_id["c044-z-offset-negative-1"].project)
        self.assertEqual(positive.z_per_pass_mm, 0.5)
        self.assertEqual(negative.z_per_pass_mm, -0.5)
        self.assertEqual(height.material_height_mm, 1)
        self.assertEqual(offset_positive.z_offset_mm, 1)
        self.assertEqual(offset_negative.z_offset_mm, -1)

    def test_rotary_cases_are_blocked_without_exported_template(self) -> None:
        rotary_cases = [case for case in CASES if case.family == "rotary-candidate"]
        self.assertEqual(len(rotary_cases), 8)
        for case in rotary_cases:
            with self.subTest(case=case.identifier):
                self.assertIsInstance(case.project, RotaryProject)
                self.assertEqual(
                    case.profile_requirements,
                    ("SaveRotaryConfig",),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "LightBurn-exported .lbrn2 template",
                ):
                    build_project(case)

    def test_rotary_clones_preserve_axis_and_unknown_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = _write_rotary_template(root / "rotary-source.lbrn2")
            source_bytes = template.read_bytes()
            output = root / "output"
            with redirect_stdout(io.StringIO()):
                generate(output, rotary_template=template)

            self.assertEqual(template.read_bytes(), source_bytes)
            manifest = json.loads((output / MANIFEST_NAME).read_text(encoding="utf-8"))
            provenance = manifest["rotary_template"]
            self.assertEqual(provenance["status"], "available")
            self.assertEqual(provenance["filename"], template.name)
            self.assertEqual(
                provenance["sha256"],
                hashlib.sha256(source_bytes).hexdigest(),
            )
            self.assertEqual(
                provenance["gantry_rotary_config"],
                {
                    "axis": "opaque-axis-token",
                    "axis_preserved_verbatim": True,
                },
            )

            expected = {
                "c045-rotary-enabled-off": ("Enabled", "False"),
                "c046-rotary-enabled-on": ("Enabled", "True"),
                "c047-rotary-object-diameter-50": (
                    "ObjectDiameter",
                    "50",
                ),
                "c048-rotary-object-diameter-75": (
                    "ObjectDiameter",
                    "75",
                ),
                "c049-rotary-mirror-output-off": (
                    "MirrorOutput",
                    "False",
                ),
                "c050-rotary-mirror-output-on": (
                    "MirrorOutput",
                    "True",
                ),
                "c051-rotary-roller": ("IsChuck", "False"),
                "c052-rotary-chuck": ("IsChuck", "True"),
            }
            items = {
                item["identifier"]: item
                for item in manifest["cases"]
                if item["family"] == "rotary-candidate"
            }
            self.assertEqual(set(items), set(expected))
            for identifier, (field, value) in expected.items():
                with self.subTest(case=identifier):
                    project_path = output / f"{identifier}.lbrn2"
                    project = ET.parse(project_path).getroot()
                    config = _rotary_config(project)
                    self.assertEqual(config.get(field), value)
                    self.assertEqual(
                        config.get("Axis"),
                        "opaque-axis-token",
                    )
                    self.assertEqual(
                        config.get("UnknownRotaryAttribute"),
                        "preserved-0",
                    )
                    self.assertIsNotNone(config.find("UnknownRotaryChild"))
                    self.assertEqual(
                        project.get("UnknownProjectAttribute"),
                        "preserved",
                    )
                    self.assertEqual(
                        project.findtext("UnknownProjectChild"),
                        "unknown content",
                    )
                    item = items[identifier]
                    self.assertEqual(item["fixture_status"], "generated")
                    self.assertEqual(item["export_status"], "pending")
                    self.assertIsNone(item["blocked_reason"])
                    self.assertEqual(
                        item["files"][project_path.name]["sha256"],
                        hashlib.sha256(project_path.read_bytes()).hexdigest(),
                    )

    def test_rotary_record_retains_template_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = _write_rotary_template(root / "rotary-source.lbrn2")
            output = root / "output"
            with redirect_stdout(io.StringIO()):
                generate(output, rotary_template=template)
            before = json.loads((output / MANIFEST_NAME).read_text(encoding="utf-8"))
            identifier = "c046-rotary-enabled-on"
            rd_path = output / f"{identifier}.rd"
            rd_path.write_bytes(b"captured rotary export")
            profile = _write_profile_evidence(root / "profile.lbdev")

            with redirect_stdout(io.StringIO()):
                record(
                    output,
                    profile_evidence=profile,
                    attest_save_rd=True,
                )

            after = json.loads((output / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(
                after["rotary_template"],
                before["rotary_template"],
            )
            item = next(
                item for item in after["cases"] if item["identifier"] == identifier
            )
            self.assertEqual(item["export_status"], "captured")
            self.assertEqual(
                item["files"][rd_path.name]["sha256"],
                hashlib.sha256(rd_path.read_bytes()).hexdigest(),
            )

    def test_rotary_template_validation_fails_before_writing(self) -> None:
        for count, message in (
            (0, "exactly one GantryRotaryConfig"),
            (2, "exactly one GantryRotaryConfig"),
        ):
            with (
                self.subTest(count=count),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                template = _write_rotary_template(
                    root / "rotary-source.lbrn2",
                    config_count=count,
                )
                output = root / "output"
                with self.assertRaisesRegex(ValueError, message):
                    generate(output, rotary_template=template)
                self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = _write_rotary_template(
                root / "rotary-source.lbrn2",
                omit_field="Axis",
            )
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "exactly one Axis field"):
                generate(output, rotary_template=template)
            self.assertFalse(output.exists())

    def test_rotary_template_cannot_be_an_output_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "c045-rotary-enabled-off.lbrn2"
            _write_rotary_template(source)
            source_bytes = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "overwrite"):
                generate(root, force=True, rotary_template=source)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_projects_use_only_evidenced_setting_fields(self) -> None:
        for case in CASES:
            with self.subTest(case=case.identifier):
                if isinstance(case.project, RotaryProject):
                    continue
                project = build_project(case)
                self.assertEqual(
                    set(project.attrib),
                    {
                        "AppVersion",
                        "FormatVersion",
                        "MaterialHeight",
                        "MirrorX",
                        "MirrorY",
                    },
                )
                if isinstance(case.project, RasterProject):
                    setting = project.find("CutSetting_Img")
                    expected_fields = RASTER_SETTING_FIELDS
                else:
                    setting = project.find("CutSetting")
                    expected_fields = VECTOR_SETTING_FIELDS
                self.assertIsNotNone(setting)
                if setting is None:
                    raise AssertionError("Missing setting")
                self.assertEqual(
                    {child.tag for child in setting},
                    expected_fields,
                )

    def test_project_values_and_embedded_png_are_exact(self) -> None:
        for case in CASES:
            with self.subTest(case=case.identifier):
                if isinstance(case.project, RotaryProject):
                    continue
                project = build_project(case)
                settings = _setting_values(project)
                self.assertEqual(
                    settings["enableLaser1"],
                    str(int(case.project.enable_laser1)),
                )
                self.assertEqual(
                    settings["enableLaser2"],
                    str(int(case.project.enable_laser2)),
                )
                self.assertEqual(
                    float(settings["minPower"]),
                    (
                        case.project.laser1_power_percent
                        if not isinstance(case.project, VectorProject)
                        or case.project.laser1_min_power_percent is None
                        else case.project.laser1_min_power_percent
                    ),
                )
                self.assertEqual(
                    float(settings["maxPower2"]),
                    case.project.laser2_power_percent,
                )
                if not isinstance(case.project, RasterProject):
                    continue
                self.assertEqual(
                    float(settings["angle"]),
                    case.project.angle_degrees,
                )
                self.assertEqual(
                    settings["bidir"],
                    str(int(case.project.bidirectional)),
                )
                self.assertEqual(
                    settings["crossHatch"],
                    str(int(case.project.cross_hatch)),
                )
                self.assertEqual(
                    float(settings["zPerPass"]),
                    case.project.z_per_pass_mm,
                )
                self.assertEqual(
                    float(settings["zOffset"]),
                    case.project.z_offset_mm,
                )
                shape = project.find("Shape")
                self.assertIsNotNone(shape)
                if shape is None:
                    raise AssertionError("Missing bitmap")
                encoded = shape.get("Data")
                self.assertIsNotNone(encoded)
                if encoded is None:
                    raise AssertionError("Missing bitmap data")
                self.assertEqual(
                    base64.b64decode(encoded, validate=True),
                    encode_grayscale_png(case.project.pixels),
                )

    def test_generate_writes_pending_deterministic_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            manifest_path = directory / MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["schema"],
                "ruida-re-lightburn-capabilities-v1",
            )
            self.assertEqual(
                manifest["scope"],
                {
                    "default_export_status": "pending",
                    "project_generation": {
                        "mode": "offline",
                        "hardware_contacted": False,
                        "lightburn_launched": False,
                    },
                    "export_capture": {
                        "attestation_scope": "per-case",
                        "available_case_count": 44,
                        "captured_case_count": 0,
                        "status": "none",
                    },
                },
            )
            self.assertEqual(
                manifest["machine"],
                {
                    "display_name": "Boss LS2040",
                    "manufacturer": "Boss Laser",
                    "model": "LS2040",
                    "rotary_hardware_available": False,
                },
            )
            self.assertEqual(manifest["rotary_template"]["status"], "required")
            self.assertEqual(
                manifest["lightburn_profile"]["display_name"],
                "Ruida 644XS",
            )
            self.assertEqual(len(manifest["cases"]), len(CASES))
            for case, item in zip(CASES, manifest["cases"], strict=True):
                with self.subTest(case=case.identifier):
                    self.assertEqual(item["identifier"], case.identifier)
                    self.assertEqual(
                        item["profile_requirements"],
                        list(case.profile_requirements),
                    )
                    self.assertEqual(
                        item["evidence"]["protocol_interpretation"],
                        "pending",
                    )
                    self.assertNotIn(item["expected_rd"], item["files"])
                    project_path = directory / item["project"]
                    if isinstance(case.project, RotaryProject):
                        self.assertEqual(item["fixture_status"], "blocked")
                        self.assertEqual(item["export_status"], "blocked")
                        self.assertIsNotNone(item["blocked_reason"])
                        self.assertFalse(project_path.exists())
                        continue
                    self.assertEqual(item["fixture_status"], "generated")
                    self.assertEqual(item["export_status"], "pending")
                    self.assertIsNone(item["blocked_reason"])
                    self.assertTrue(project_path.is_file())
                    self.assertEqual(
                        item["files"][project_path.name]["sha256"],
                        hashlib.sha256(project_path.read_bytes()).hexdigest(),
                    )
                    ET.parse(project_path)

    def test_generation_is_byte_deterministic(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_temporary,
            tempfile.TemporaryDirectory() as second_temporary,
        ):
            first = Path(first_temporary)
            second = Path(second_temporary)
            with redirect_stdout(io.StringIO()):
                generate(first)
                generate(second)
            self.assertEqual(
                sorted(path.name for path in first.iterdir()),
                sorted(path.name for path in second.iterdir()),
            )
            for first_path in first.iterdir():
                self.assertEqual(
                    first_path.read_bytes(),
                    (second / first_path.name).read_bytes(),
                )

    def test_record_hashes_exports_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            captured = CASES[0]
            rd_path = directory / f"{captured.identifier}.rd"
            rd_path.write_bytes(captured.identifier.encode("ascii"))
            profile = _write_profile_evidence(directory / "profile.lbdev")
            with redirect_stdout(io.StringIO()):
                record(
                    directory,
                    profile_evidence=profile,
                    attest_save_rd=True,
                )
            manifest = json.loads(
                (directory / MANIFEST_NAME).read_text(encoding="utf-8")
            )
            captured_item = manifest["cases"][0]
            self.assertEqual(captured_item["export_status"], "captured")
            self.assertEqual(
                captured_item["files"][rd_path.name]["sha256"],
                hashlib.sha256(rd_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                captured_item["capture"]["project_sha256"],
                captured_item["files"][captured_item["project"]]["sha256"],
            )
            self.assertEqual(
                captured_item["capture"]["profile_evidence"]["evidence_sha256"],
                hashlib.sha256(profile.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                captured_item["capture"]["export_attestation"],
                {
                    "controller_connection": "not-attested",
                    "job_transmitted": False,
                    "lightburn_launched": True,
                    "machine_file_action": "save-rd",
                },
            )
            self.assertEqual(
                manifest["scope"]["export_capture"],
                {
                    "attestation_scope": "per-case",
                    "available_case_count": 44,
                    "captured_case_count": 1,
                    "status": "partial",
                },
            )
            for case, item in zip(
                CASES[1:],
                manifest["cases"][1:],
                strict=True,
            ):
                expected = (
                    "blocked" if isinstance(case.project, RotaryProject) else "pending"
                )
                self.assertEqual(item["export_status"], expected)
                self.assertNotIn(item["expected_rd"], item["files"])

    def test_new_export_requires_profile_and_offline_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            case = CASES[0]
            (directory / f"{case.identifier}.rd").write_bytes(b"export")
            profile = _write_profile_evidence(directory / "profile.lbdev")

            with self.assertRaisesRegex(ValueError, "explicit LightBurn"):
                record(directory, profile_evidence=profile)
            with self.assertRaisesRegex(ValueError, "profile evidence"):
                record(directory, attest_save_rd=True)

    def test_profile_requirements_are_enforced_for_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            case = _by_id()["c043-z-offset-positive-1"]
            (directory / f"{case.identifier}.rd").write_bytes(b"z export")
            profile = _write_profile_evidence(
                directory / "profile.lbdev",
                EnableZ=False,
            )
            with self.assertRaisesRegex(ValueError, "does not enable EnableZ"):
                record(
                    directory,
                    profile_evidence=profile,
                    attest_save_rd=True,
                )

    def test_capture_profile_must_identify_ruida_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            case = CASES[0]
            (directory / f"{case.identifier}.rd").write_bytes(b"export")
            profile = _write_profile_evidence(directory / "profile.lbdev")
            document = json.loads(profile.read_text(encoding="utf-8"))
            document["DeviceList"][0]["Name"] = "GRBL"
            profile.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly 'Ruida'"):
                record(
                    directory,
                    profile_evidence=profile,
                    attest_save_rd=True,
                )

    def test_comparison_captures_require_identical_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            baseline = _by_id()["c001-raster-angle-000-uni"]
            variant = _by_id()["c002-raster-angle-045-uni"]
            (directory / f"{baseline.identifier}.rd").write_bytes(b"baseline")
            first_profile = _write_profile_evidence(directory / "first.lbdev")
            with redirect_stdout(io.StringIO()):
                record(
                    directory,
                    profile_evidence=first_profile,
                    attest_save_rd=True,
                )

            (directory / f"{variant.identifier}.rd").write_bytes(b"variant")
            second_profile = _write_profile_evidence(
                directory / "second.lbdev",
                EnableZ=False,
            )
            with self.assertRaisesRegex(
                ValueError,
                "differs across controlled comparison",
            ):
                record(
                    directory,
                    profile_evidence=second_profile,
                    attest_save_rd=True,
                )

    def test_record_rejects_stale_export_after_project_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            case = CASES[0]
            project_path = directory / f"{case.identifier}.lbrn2"
            rd_path = directory / f"{case.identifier}.rd"
            rd_path.write_bytes(b"first export")
            profile = _write_profile_evidence(directory / "profile.lbdev")
            with redirect_stdout(io.StringIO()):
                record(
                    directory,
                    profile_evidence=profile,
                    attest_save_rd=True,
                )

            project_path.write_bytes(project_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "Stale RD export"):
                record(directory)

            rd_path.write_bytes(b"second export")
            with redirect_stdout(io.StringIO()):
                record(
                    directory,
                    profile_evidence=profile,
                    attest_save_rd=True,
                )

    def test_profile_matrix_variant_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            case = CASES[0]
            (directory / f"{case.identifier}.rd").write_bytes(b"export")
            matrix = directory / "profile-matrix.json"
            variant_profile = directory / "research-enable-z.lbdev"
            _write_profile_evidence(variant_profile)
            variant_bytes = variant_profile.read_bytes()
            variant_sha256 = hashlib.sha256(variant_bytes).hexdigest()
            matrix.write_text(
                json.dumps(
                    {
                        "schema": "ruida-re.lightburn-profile-matrix",
                        "schema_version": 1,
                        "source": {
                            "controller_identity": {
                                "field": "DeviceList[0].Name",
                                "value": "Ruida",
                            },
                            "profile_type": "Serial",
                            "filename": "source.lbdev",
                            "source_sha256": "a" * 64,
                            "source_document_sha256": "c" * 64,
                        },
                        "variants": [
                            {
                                "identifier": "enable-z",
                                "changed_key": "EnableZ",
                                "filename": variant_profile.name,
                                "target_value": True,
                                "variant_sha256": variant_sha256,
                            }
                        ],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "explicit profile variant"):
                record(
                    directory,
                    profile_evidence=matrix,
                    attest_save_rd=True,
                )
            variant_profile.write_bytes(b"changed profile variant")
            with self.assertRaisesRegex(ValueError, "does not match artifact"):
                record(
                    directory,
                    profile_evidence=matrix,
                    profile_variant="enable-z",
                    attest_save_rd=True,
                )
            variant_profile.write_bytes(variant_bytes)
            with redirect_stdout(io.StringIO()):
                record(
                    directory,
                    profile_evidence=matrix,
                    profile_variant="enable-z",
                    attest_save_rd=True,
                )
            manifest = json.loads(
                (directory / MANIFEST_NAME).read_text(encoding="utf-8")
            )
            evidence = manifest["cases"][0]["capture"]["profile_evidence"]
            self.assertEqual(evidence["kind"], "lightburn-profile-matrix")
            self.assertEqual(evidence["identifier"], "enable-z")
            self.assertEqual(evidence["profile_sha256"], variant_sha256)

    def test_generate_refuses_to_rebind_existing_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            project_path = directory / f"{CASES[0].identifier}.lbrn2"
            manifest_path = directory / MANIFEST_NAME
            project_bytes = project_path.read_bytes()
            manifest_bytes = manifest_path.read_bytes()
            (directory / f"{CASES[0].identifier}.rd").write_bytes(b"export")
            with self.assertRaisesRegex(FileExistsError, "existing RD export"):
                generate(directory, force=True)
            self.assertEqual(project_path.read_bytes(), project_bytes)
            self.assertEqual(manifest_path.read_bytes(), manifest_bytes)

    def test_generation_does_not_clobber_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with redirect_stdout(io.StringIO()):
                generate(directory)
            with self.assertRaises(FileExistsError):
                generate(directory)
            with redirect_stdout(io.StringIO()):
                generate(directory, force=True)


if __name__ == "__main__":
    unittest.main()
