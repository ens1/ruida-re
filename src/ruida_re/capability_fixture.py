"""Generate controlled LightBurn advanced-capability fixtures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal
from xml.etree import ElementTree as ET

from .cli_io import atomic_write_bytes, atomic_write_text
from .fixture import (
    DEFAULT_FIXTURE_ROOT,
    LIGHTBURN_APP_SHA256,
    project_stage,
)
from .raster_fixture import (
    BINARY_PIXELS,
    GRAYSCALE_PIXELS,
    encode_grayscale_png,
)

CAPABILITY_DIR = DEFAULT_FIXTURE_ROOT / "capabilities"
MANIFEST_NAME = "capabilities.json"
PROMOTION_SCHEMA = "ruida-re.capability-publication.v1"
PROMOTED_FAMILIES = (
    "cut-through",
    "diagonal-raster",
    "dot-mode",
    "dwell",
    "dynamic-vector-power",
    "frequency",
    "laser-channel",
    "pulse-width-candidate",
    "z-axis-candidate",
)
ROTARY_BLOCKED_REASON = (
    "Supply a LightBurn-exported .lbrn2 project containing exactly one "
    "GantryRotaryConfig; its Axis value and unknown content will be "
    "preserved rather than inferred."
)

_PROFILE_DIRECTORY = Path("profiles")
_PATH_KEY = re.compile(r"(?:path|directory|folder)$", re.IGNORECASE)
_REDACTED_PATH = "<redacted-local-path>"

Family = Literal[
    "diagonal-raster",
    "dot-mode",
    "dynamic-vector-power",
    "dwell",
    "cut-through",
    "frequency",
    "laser-channel",
    "pulse-width-candidate",
    "rotary-candidate",
    "z-axis-candidate",
]
EvidenceStatus = Literal["controlled-input", "hypothesis"]
ProfileRequirement = Literal[
    "EnableZ",
    "Laser1IsFiber",
    "Laser1IsRFTube",
    "Laser2Enabled",
    "SaveRotaryConfig",
]


@dataclass(frozen=True)
class RasterProject:
    """The independently variable controls for one bitmap project."""

    pixels: tuple[tuple[int, ...], ...] = BINARY_PIXELS
    width_mm: float = 4
    height_mm: float = 2
    dither_mode: str = "threshold"
    laser1_power_percent: float = 50
    laser2_power_percent: float = 50
    enable_laser1: bool = True
    enable_laser2: bool = False
    speed_mm_s: float = 100
    interval_mm: float = 0.5
    angle_degrees: float = 0
    bidirectional: bool = False
    cross_hatch: bool = False
    num_passes: int = 1
    z_per_pass_mm: float = 0
    z_offset_mm: float = 0
    material_height_mm: float = 0


@dataclass(frozen=True)
class VectorProject:
    """The independently variable controls for one vector project."""

    start_mm: tuple[float, float] = (20, 20)
    end_mm: tuple[float, float] = (30, 20)
    laser1_power_percent: float = 20
    laser1_min_power_percent: float | None = None
    laser2_power_percent: float = 40
    enable_laser1: bool = True
    enable_laser2: bool = False
    speed_mm_s: float = 10
    power_scale_sequence: tuple[float | None, ...] = (None,)
    start_delay_ms: float = 0
    end_delay_ms: float = 0
    through_power_percent: float = 0
    through_power2_percent: float = 0
    enable_cut_through_start: bool = False
    enable_cut_through_end: bool = False
    dot_mode: bool = False
    dot_time_ms: float = 0
    dot_spacing_mm: float = 0
    override_frequency: bool = False
    frequency_hz: int = 20_000
    fiber_pulse_width_ns: float = 0


@dataclass(frozen=True)
class RotaryProject:
    """One field to vary in an exported LightBurn rotary template."""

    enabled: bool | None = None
    object_diameter_mm: float | None = None
    mirror_output: bool | None = None
    is_chuck: bool | None = None


Project = RasterProject | RotaryProject | VectorProject


@dataclass(frozen=True)
class CapabilityCase:
    """One project and its controlled relationship to another project."""

    identifier: str
    family: Family
    purpose: str
    project: Project
    baseline: str | None = None
    independent_variable: str | None = None
    evidence_status: EvidenceStatus = "controlled-input"
    hypothesis: str | None = None
    profile_requirements: tuple[ProfileRequirement, ...] = ()


RASTER_BASELINE = RasterProject()
LASER_BASELINE = VectorProject()
Z_BASELINE = RasterProject(
    pixels=GRAYSCALE_PIXELS,
    height_mm=1,
    dither_mode="3dslice",
    num_passes=4,
)


CASES = (
    CapabilityCase(
        "c001-raster-angle-000-uni",
        "diagonal-raster",
        "establish the horizontal unidirectional threshold baseline",
        RASTER_BASELINE,
    ),
    CapabilityCase(
        "c002-raster-angle-045-uni",
        "diagonal-raster",
        "isolate a 45 degree unidirectional scan angle",
        RasterProject(angle_degrees=45),
        "c001-raster-angle-000-uni",
        "angle_degrees",
    ),
    CapabilityCase(
        "c003-raster-angle-045-bi",
        "diagonal-raster",
        "isolate bidirectional scanning at 45 degrees",
        RasterProject(angle_degrees=45, bidirectional=True),
        "c002-raster-angle-045-uni",
        "bidirectional",
    ),
    CapabilityCase(
        "c004-raster-angle-135-uni",
        "diagonal-raster",
        "isolate the direction change from 45 to 135 degrees",
        RasterProject(angle_degrees=135),
        "c002-raster-angle-045-uni",
        "angle_degrees",
    ),
    CapabilityCase(
        "c005-raster-angle-045-cross-hatch",
        "diagonal-raster",
        "isolate LightBurn's cross-hatch layer control at 45 degrees",
        RasterProject(angle_degrees=45, cross_hatch=True),
        "c002-raster-angle-045-uni",
        "cross_hatch",
    ),
    CapabilityCase(
        "c006-laser1-only-static",
        "laser-channel",
        "establish laser 1 enabled with distinct inactive laser 2 power",
        LASER_BASELINE,
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c007-lasers-1-and-2-static",
        "laser-channel",
        "isolate enabling laser 2 while laser 1 remains enabled",
        VectorProject(enable_laser2=True),
        "c006-laser1-only-static",
        "enable_laser2",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c008-laser2-only-static",
        "laser-channel",
        "isolate disabling laser 1 while laser 2 remains enabled",
        VectorProject(enable_laser1=False, enable_laser2=True),
        "c007-lasers-1-and-2-static",
        "enable_laser1",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c009-lasers-both-laser1-power-25",
        "laser-channel",
        "isolate laser 1 static power from laser 2 static power",
        VectorProject(
            laser1_power_percent=25,
            enable_laser2=True,
        ),
        "c007-lasers-1-and-2-static",
        "laser1_power_percent",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c010-lasers-both-laser2-power-45",
        "laser-channel",
        "isolate laser 2 static power from laser 1 static power",
        VectorProject(
            laser2_power_percent=45,
            enable_laser2=True,
        ),
        "c007-lasers-1-and-2-static",
        "laser2_power_percent",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c011-z-per-pass-zero-baseline",
        "z-axis-candidate",
        "establish a four-pass 3D-slice project with no Z step",
        Z_BASELINE,
        evidence_status="hypothesis",
        hypothesis=(
            "zPerPass is LightBurn project metadata whose Ruida output "
            "effect is not yet established"
        ),
        profile_requirements=("EnableZ",),
    ),
    CapabilityCase(
        "c012-z-per-pass-positive-05",
        "z-axis-candidate",
        "test whether a positive zPerPass emits inter-pass Z motion",
        RasterProject(
            pixels=GRAYSCALE_PIXELS,
            height_mm=1,
            dither_mode="3dslice",
            num_passes=4,
            z_per_pass_mm=0.5,
        ),
        "c011-z-per-pass-zero-baseline",
        "z_per_pass_mm",
        "hypothesis",
        "a positive zPerPass may emit positive inter-pass Z motion",
        ("EnableZ",),
    ),
    CapabilityCase(
        "c013-z-per-pass-negative-05",
        "z-axis-candidate",
        "test whether a negative zPerPass emits inter-pass Z motion",
        RasterProject(
            pixels=GRAYSCALE_PIXELS,
            height_mm=1,
            dither_mode="3dslice",
            num_passes=4,
            z_per_pass_mm=-0.5,
        ),
        "c011-z-per-pass-zero-baseline",
        "z_per_pass_mm",
        "hypothesis",
        "a negative zPerPass may emit negative inter-pass Z motion",
        ("EnableZ",),
    ),
    CapabilityCase(
        "c014-material-height-positive-1",
        "z-axis-candidate",
        "test MaterialHeight as a possible job-level Z input",
        RasterProject(
            pixels=GRAYSCALE_PIXELS,
            height_mm=1,
            dither_mode="3dslice",
            num_passes=4,
            material_height_mm=1,
        ),
        "c011-z-per-pass-zero-baseline",
        "material_height_mm",
        "hypothesis",
        "MaterialHeight may influence emitted job-level Z state",
        ("EnableZ",),
    ),
    CapabilityCase(
        "c015-power-scale-omitted",
        "dynamic-vector-power",
        "establish three touching shapes without PowerScale attributes",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(None, None, None),
        ),
    ),
    CapabilityCase(
        "c016-power-scale-all-000",
        "dynamic-vector-power",
        "isolate explicit zero PowerScale on each touching shape",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(0, 0, 0),
        ),
        "c015-power-scale-omitted",
        "power_scale_sequence",
    ),
    CapabilityCase(
        "c017-power-scale-all-050",
        "dynamic-vector-power",
        "isolate explicit 50 percent PowerScale on each touching shape",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(50, 50, 50),
        ),
        "c015-power-scale-omitted",
        "power_scale_sequence",
    ),
    CapabilityCase(
        "c018-power-scale-all-100",
        "dynamic-vector-power",
        "isolate explicit 100 percent PowerScale on each touching shape",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(100, 100, 100),
        ),
        "c015-power-scale-omitted",
        "power_scale_sequence",
    ),
    CapabilityCase(
        "c019-power-scale-sequence-000-050-100",
        "dynamic-vector-power",
        "observe increasing PowerScale across three touching shapes",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(0, 50, 100),
        ),
        "c015-power-scale-omitted",
        "power_scale_sequence",
    ),
    CapabilityCase(
        "c020-power-scale-sequence-100-050-000",
        "dynamic-vector-power",
        "observe the same three PowerScale values in reverse order",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(100, 50, 0),
        ),
        "c019-power-scale-sequence-000-050-100",
        "power_scale_sequence",
    ),
    CapabilityCase(
        "c021-power-scale-sequence-repeated-050",
        "dynamic-vector-power",
        "observe consecutive repeated 50 percent PowerScale states",
        VectorProject(
            laser1_power_percent=70,
            laser1_min_power_percent=10,
            power_scale_sequence=(0, 50, 50),
        ),
        "c019-power-scale-sequence-000-050-100",
        "power_scale_sequence",
    ),
    CapabilityCase(
        "c022-delay-zero-baseline",
        "dwell",
        "establish zero start and end delay",
        VectorProject(),
    ),
    CapabilityCase(
        "c023-start-delay-100",
        "dwell",
        "isolate a 100 ms layer start delay",
        VectorProject(start_delay_ms=100),
        "c022-delay-zero-baseline",
        "start_delay_ms",
    ),
    CapabilityCase(
        "c024-start-delay-200",
        "dwell",
        "isolate a 200 ms layer start delay",
        VectorProject(start_delay_ms=200),
        "c022-delay-zero-baseline",
        "start_delay_ms",
    ),
    CapabilityCase(
        "c025-end-delay-100",
        "dwell",
        "isolate a 100 ms layer end delay",
        VectorProject(end_delay_ms=100),
        "c022-delay-zero-baseline",
        "end_delay_ms",
    ),
    CapabilityCase(
        "c026-end-delay-200",
        "dwell",
        "isolate a 200 ms layer end delay",
        VectorProject(end_delay_ms=200),
        "c022-delay-zero-baseline",
        "end_delay_ms",
    ),
    CapabilityCase(
        "c027-cut-through-disabled",
        "cut-through",
        "establish configured through powers with both controls disabled",
        VectorProject(
            through_power_percent=25,
            through_power2_percent=45,
        ),
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c028-cut-through-start-enabled",
        "cut-through",
        "isolate enabling cut-through at shape start",
        VectorProject(
            through_power_percent=25,
            through_power2_percent=45,
            enable_cut_through_start=True,
        ),
        "c027-cut-through-disabled",
        "enable_cut_through_start",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c029-cut-through-end-enabled",
        "cut-through",
        "isolate enabling cut-through at shape end",
        VectorProject(
            through_power_percent=25,
            through_power2_percent=45,
            enable_cut_through_end=True,
        ),
        "c027-cut-through-disabled",
        "enable_cut_through_end",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c030-cut-through-start-and-end",
        "cut-through",
        "isolate adding end cut-through to start cut-through",
        VectorProject(
            through_power_percent=25,
            through_power2_percent=45,
            enable_cut_through_start=True,
            enable_cut_through_end=True,
        ),
        "c028-cut-through-start-enabled",
        "enable_cut_through_end",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c031-cut-through-laser1-power-35",
        "cut-through",
        "isolate laser 1 through power with both endpoints enabled",
        VectorProject(
            through_power_percent=35,
            through_power2_percent=45,
            enable_cut_through_start=True,
            enable_cut_through_end=True,
        ),
        "c030-cut-through-start-and-end",
        "through_power_percent",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c032-cut-through-laser2-power-55",
        "cut-through",
        "isolate laser 2 through power with both endpoints enabled",
        VectorProject(
            through_power_percent=25,
            through_power2_percent=55,
            enable_cut_through_start=True,
            enable_cut_through_end=True,
        ),
        "c030-cut-through-start-and-end",
        "through_power2_percent",
        profile_requirements=("Laser2Enabled",),
    ),
    CapabilityCase(
        "c033-dot-mode-disabled",
        "dot-mode",
        "establish configured dot timing and spacing with dot mode off",
        VectorProject(dot_time_ms=100, dot_spacing_mm=1),
    ),
    CapabilityCase(
        "c034-dot-mode-enabled",
        "dot-mode",
        "isolate enabling LightBurn dot mode",
        VectorProject(
            dot_mode=True,
            dot_time_ms=100,
            dot_spacing_mm=1,
        ),
        "c033-dot-mode-disabled",
        "dot_mode",
    ),
    CapabilityCase(
        "c035-dot-time-200",
        "dot-mode",
        "isolate a 200 ms dot time while dot mode is enabled",
        VectorProject(
            dot_mode=True,
            dot_time_ms=200,
            dot_spacing_mm=1,
        ),
        "c034-dot-mode-enabled",
        "dot_time_ms",
    ),
    CapabilityCase(
        "c036-dot-spacing-2",
        "dot-mode",
        "isolate 2 mm dot spacing while dot mode is enabled",
        VectorProject(
            dot_mode=True,
            dot_time_ms=100,
            dot_spacing_mm=2,
        ),
        "c034-dot-mode-enabled",
        "dot_spacing_mm",
    ),
    CapabilityCase(
        "c037-frequency-override-disabled-20000",
        "frequency",
        "establish 20000 Hz with frequency override disabled",
        VectorProject(frequency_hz=20_000),
        profile_requirements=("Laser1IsRFTube",),
    ),
    CapabilityCase(
        "c038-frequency-override-enabled-20000",
        "frequency",
        "isolate enabling the 20000 Hz frequency override",
        VectorProject(override_frequency=True, frequency_hz=20_000),
        "c037-frequency-override-disabled-20000",
        "override_frequency",
        profile_requirements=("Laser1IsRFTube",),
    ),
    CapabilityCase(
        "c039-frequency-override-enabled-10000",
        "frequency",
        "isolate a 10000 Hz override from the 20000 Hz case",
        VectorProject(override_frequency=True, frequency_hz=10_000),
        "c038-frequency-override-enabled-20000",
        "frequency_hz",
        profile_requirements=("Laser1IsRFTube",),
    ),
    CapabilityCase(
        "c040-fiber-pulse-width-zero",
        "pulse-width-candidate",
        "establish the serialized zero fiberPulseWidth value",
        VectorProject(),
        evidence_status="hypothesis",
        hypothesis=(
            "fiberPulseWidth may be ignored by Ruida job export even when "
            "the LightBurn profile marks laser 1 as fiber"
        ),
        profile_requirements=("Laser1IsFiber",),
    ),
    CapabilityCase(
        "c041-fiber-pulse-width-100",
        "pulse-width-candidate",
        "test a 100 ns fiberPulseWidth value",
        VectorProject(fiber_pulse_width_ns=100),
        "c040-fiber-pulse-width-zero",
        "fiber_pulse_width_ns",
        "hypothesis",
        "fiberPulseWidth may produce a job-level pulse-width command",
        ("Laser1IsFiber",),
    ),
    CapabilityCase(
        "c042-fiber-pulse-width-200",
        "pulse-width-candidate",
        "test a 200 ns fiberPulseWidth value",
        VectorProject(fiber_pulse_width_ns=200),
        "c040-fiber-pulse-width-zero",
        "fiber_pulse_width_ns",
        "hypothesis",
        "fiberPulseWidth may produce a job-level pulse-width command",
        ("Laser1IsFiber",),
    ),
    CapabilityCase(
        "c043-z-offset-positive-1",
        "z-axis-candidate",
        "test whether positive zOffset emits job-level Z motion",
        RasterProject(
            pixels=GRAYSCALE_PIXELS,
            height_mm=1,
            dither_mode="3dslice",
            num_passes=4,
            z_offset_mm=1,
        ),
        "c011-z-per-pass-zero-baseline",
        "z_offset_mm",
        "hypothesis",
        "a positive zOffset may emit positive job-level Z motion",
        ("EnableZ",),
    ),
    CapabilityCase(
        "c044-z-offset-negative-1",
        "z-axis-candidate",
        "test whether negative zOffset emits job-level Z motion",
        RasterProject(
            pixels=GRAYSCALE_PIXELS,
            height_mm=1,
            dither_mode="3dslice",
            num_passes=4,
            z_offset_mm=-1,
        ),
        "c011-z-per-pass-zero-baseline",
        "z_offset_mm",
        "hypothesis",
        "a negative zOffset may emit negative job-level Z motion",
        ("EnableZ",),
    ),
    CapabilityCase(
        "c045-rotary-enabled-off",
        "rotary-candidate",
        "set Enabled off in an exported rotary template",
        RotaryProject(enabled=False),
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c046-rotary-enabled-on",
        "rotary-candidate",
        "isolate enabling the exported rotary configuration",
        RotaryProject(enabled=True),
        "c045-rotary-enabled-off",
        "enabled",
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c047-rotary-object-diameter-50",
        "rotary-candidate",
        "set ObjectDiameter to 50 mm in an exported rotary template",
        RotaryProject(object_diameter_mm=50),
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c048-rotary-object-diameter-75",
        "rotary-candidate",
        "isolate ObjectDiameter at 75 mm",
        RotaryProject(object_diameter_mm=75),
        "c047-rotary-object-diameter-50",
        "object_diameter_mm",
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c049-rotary-mirror-output-off",
        "rotary-candidate",
        "set MirrorOutput off in an exported rotary template",
        RotaryProject(mirror_output=False),
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c050-rotary-mirror-output-on",
        "rotary-candidate",
        "isolate enabling mirrored rotary output",
        RotaryProject(mirror_output=True),
        "c049-rotary-mirror-output-off",
        "mirror_output",
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c051-rotary-roller",
        "rotary-candidate",
        "set IsChuck off in an exported rotary template",
        RotaryProject(is_chuck=False),
        profile_requirements=("SaveRotaryConfig",),
    ),
    CapabilityCase(
        "c052-rotary-chuck",
        "rotary-candidate",
        "isolate setting IsChuck on",
        RotaryProject(is_chuck=True),
        "c051-rotary-roller",
        "is_chuck",
        profile_requirements=("SaveRotaryConfig",),
    ),
)


def _project_values(project: Project) -> dict[str, object]:
    return asdict(project)


def controlled_differences(
    baseline: CapabilityCase,
    case: CapabilityCase,
) -> set[str]:
    """Return independently changed project controls for a case pair."""
    if type(baseline.project) is not type(case.project):
        raise ValueError("Compared cases must use the same project kind")
    baseline_values = _project_values(baseline.project)
    case_values = _project_values(case.project)
    return {
        name for name, value in case_values.items() if baseline_values[name] != value
    }


def validate_cases(cases: tuple[CapabilityCase, ...] = CASES) -> None:
    """Validate identifiers, hypotheses, and one-variable comparisons."""
    indexed: dict[str, CapabilityCase] = {}
    for case in cases:
        if case.identifier in indexed:
            raise ValueError(f"Duplicate case identifier: {case.identifier}")
        if isinstance(case.project, VectorProject):
            scales = case.project.power_scale_sequence
            if not scales:
                raise ValueError(f"Missing vector shape sequence: {case.identifier}")
            if any(scale is not None and not 0 <= scale <= 100 for scale in scales):
                raise ValueError(f"PowerScale outside 0 through 100: {case.identifier}")
        if isinstance(case.project, RotaryProject):
            mutation_count = sum(
                value is not None for value in _project_values(case.project).values()
            )
            if mutation_count != 1:
                raise ValueError(
                    f"Rotary case must change exactly one field: {case.identifier}"
                )
        if case.evidence_status == "hypothesis" and not case.hypothesis:
            raise ValueError(f"Missing hypothesis: {case.identifier}")
        if case.evidence_status != "hypothesis" and case.hypothesis:
            raise ValueError(f"Unexpected hypothesis: {case.identifier}")
        if case.baseline is None:
            if case.independent_variable is not None:
                raise ValueError(
                    f"Root case has an independent variable: {case.identifier}"
                )
        else:
            if case.baseline not in indexed:
                raise ValueError(
                    f"Unknown or forward baseline for {case.identifier}: "
                    f"{case.baseline}"
                )
            baseline = indexed[case.baseline]
            if baseline.profile_requirements != case.profile_requirements:
                raise ValueError(
                    f"Uncontrolled profile requirements for {case.identifier}"
                )
            differences = controlled_differences(
                baseline,
                case,
            )
            expected = {case.independent_variable}
            if differences != expected:
                raise ValueError(
                    f"Uncontrolled comparison for {case.identifier}: "
                    f"{sorted(differences)}"
                )
        indexed[case.identifier] = case


def _number(value: float) -> str:
    return f"{value:g}"


def _value(parent: ET.Element, name: str, value: object) -> None:
    ET.SubElement(parent, name, Value=str(value))


def _project_root(material_height_mm: float = 0) -> ET.Element:
    return ET.Element(
        "LightBurnProject",
        AppVersion="2.1.03",
        FormatVersion="1",
        MaterialHeight=_number(material_height_mm),
        MirrorX="False",
        MirrorY="False",
    )


def _build_raster_project(
    case: CapabilityCase,
    project_spec: RasterProject,
) -> ET.Element:
    project = _project_root(project_spec.material_height_mm)
    setting = ET.SubElement(project, "CutSetting_Img", type="Image")
    values = {
        "index": 0,
        "name": "C00",
        "minPower": project_spec.laser1_power_percent,
        "maxPower": project_spec.laser1_power_percent,
        "minPower2": project_spec.laser2_power_percent,
        "maxPower2": project_spec.laser2_power_percent,
        "speed": project_spec.speed_mm_s,
        "enableLaser1": int(project_spec.enable_laser1),
        "enableLaser2": int(project_spec.enable_laser2),
        "priority": 0,
        "doOutput": 1,
        "runBlower": 0,
        "autoBlower": 0,
        "numPasses": project_spec.num_passes,
        "zPerPass": project_spec.z_per_pass_mm,
        "zOffset": project_spec.z_offset_mm,
        "scanOpt": "individual",
        "bidir": int(project_spec.bidirectional),
        "crossHatch": int(project_spec.cross_hatch),
        "overscan": 0,
        "overscanPercent": 0,
        "interval": project_spec.interval_mm,
        "angle": project_spec.angle_degrees,
        "negative": 0,
        "passThrough": 0,
        "ditherMode": project_spec.dither_mode,
        "cleanupPass": 0,
        "dpi": 25.4 / project_spec.interval_mm,
        "linkDPItoInterval": 1,
    }
    for name, value in values.items():
        _value(setting, name, value)

    png = encode_grayscale_png(project_spec.pixels)
    shape = ET.SubElement(
        project,
        "Shape",
        Type="Bitmap",
        CutIndex="0",
        W=_number(project_spec.width_mm),
        H=_number(project_spec.height_mm),
        Gamma="1",
        Contrast="0",
        Brightness="0",
        EnhanceAmount="0",
        EnhanceRadius="0",
        EnhanceDenoise="0",
        File=f"{case.identifier}.png",
        SourceHash="0",
        Data=base64.b64encode(png).decode("ascii"),
    )
    center_x = 20 + project_spec.width_mm / 2
    center_y = 20 + project_spec.height_mm / 2
    ET.SubElement(
        shape, "XForm"
    ).text = f"1 0 0 1 {_number(center_x)} {_number(center_y)}"
    ET.SubElement(project, "Notes", ShowOnLoad="0", Notes="")
    return project


def _build_vector_project(project_spec: VectorProject) -> ET.Element:
    project = _project_root()
    setting = ET.SubElement(project, "CutSetting", type="Cut")
    minimum_power = project_spec.laser1_min_power_percent
    if minimum_power is None:
        minimum_power = project_spec.laser1_power_percent
    values = {
        "index": 0,
        "name": "C00",
        "minPower": minimum_power,
        "maxPower": project_spec.laser1_power_percent,
        "minPower2": project_spec.laser2_power_percent,
        "maxPower2": project_spec.laser2_power_percent,
        "speed": project_spec.speed_mm_s,
        "enableLaser1": int(project_spec.enable_laser1),
        "enableLaser2": int(project_spec.enable_laser2),
        "startDelay": project_spec.start_delay_ms,
        "endDelay": project_spec.end_delay_ms,
        "throughPower": project_spec.through_power_percent,
        "throughPower2": project_spec.through_power2_percent,
        "enableCutThroughStart": int(project_spec.enable_cut_through_start),
        "enableCutThroughEnd": int(project_spec.enable_cut_through_end),
        "frequency": project_spec.frequency_hz,
        "overrideFrequency": int(project_spec.override_frequency),
        "fiberPulseWidth": project_spec.fiber_pulse_width_ns,
        "priority": 0,
        "doOutput": 1,
        "runBlower": 0,
        "autoBlower": 0,
        "numPasses": 1,
        "dotMode": int(project_spec.dot_mode),
        "dotTime": project_spec.dot_time_ms,
        "dotSpacing": project_spec.dot_spacing_mm,
    }
    for name, value in values.items():
        _value(setting, name, value)

    dx = project_spec.end_mm[0] - project_spec.start_mm[0]
    dy = project_spec.end_mm[1] - project_spec.start_mm[1]
    start_x, start_y = project_spec.start_mm
    for index, power_scale in enumerate(project_spec.power_scale_sequence):
        attributes = {"Type": "Path", "CutIndex": "0"}
        if power_scale is not None:
            attributes["PowerScale"] = _number(power_scale)
        shape = ET.SubElement(project, "Shape", attrib=attributes)
        ET.SubElement(shape, "VertList").text = f"V 0 0 V {_number(dx)} {_number(dy)}"
        ET.SubElement(shape, "PrimList").text = "L 0 1"
        shape_x = start_x + index * dx
        shape_y = start_y + index * dy
        ET.SubElement(
            shape, "XForm"
        ).text = f"1 0 0 1 {_number(shape_x)} {_number(shape_y)}"
    ET.SubElement(project, "Notes", ShowOnLoad="0", Notes="")
    return project


def _rotary_field(
    config: ET.Element,
    name: str,
) -> tuple[Literal["attribute", "value", "text"], ET.Element, str]:
    if name in config.attrib:
        return "attribute", config, config.attrib[name]
    children = [child for child in config if child.tag == name]
    if len(children) != 1:
        raise ValueError(f"GantryRotaryConfig must contain exactly one {name} field")
    child = children[0]
    if "Value" in child.attrib:
        return "value", child, child.attrib["Value"]
    if child.text is not None:
        return "text", child, child.text
    raise ValueError(f"GantryRotaryConfig {name} has no value")


def _rotary_config(project: ET.Element) -> ET.Element:
    configs = [
        element for element in project.iter() if element.tag == "GantryRotaryConfig"
    ]
    if len(configs) != 1:
        raise ValueError("Rotary template must contain exactly one GantryRotaryConfig")
    return configs[0]


def _boolean_like(source: str, value: bool) -> str:
    lowered = source.lower()
    if lowered in {"0", "1"}:
        return str(int(value))
    if lowered not in {"false", "true"}:
        raise ValueError(f"Unsupported LightBurn boolean value: {source!r}")
    target = "true" if value else "false"
    if source.isupper():
        return target.upper()
    if source[:1].isupper():
        return target.capitalize()
    return target


def _set_rotary_field(
    config: ET.Element,
    name: str,
    value: bool | float,
) -> None:
    location, element, source = _rotary_field(config, name)
    encoded = (
        _boolean_like(source, value) if isinstance(value, bool) else _number(value)
    )
    if location == "attribute":
        element.attrib[name] = encoded
    elif location == "value":
        element.attrib["Value"] = encoded
    else:
        element.text = encoded


def _parse_rotary_template(path: Path) -> tuple[ET.Element, dict[str, object]]:
    if path.suffix.lower() != ".lbrn2":
        raise ValueError(f"Rotary template must end in .lbrn2: {path}")
    raw = path.read_bytes()
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        project = ET.fromstring(raw, parser=parser)
    except ET.ParseError as error:
        raise ValueError(f"Invalid rotary LightBurn template: {error}") from error
    if project.tag != "LightBurnProject":
        raise ValueError("Rotary template root must be LightBurnProject")
    config = _rotary_config(project)
    required_fields = (
        "Axis",
        "Enabled",
        "ObjectDiameter",
        "MirrorOutput",
        "IsChuck",
    )
    fields = {name: _rotary_field(config, name)[2] for name in required_fields}
    for name in ("Enabled", "MirrorOutput", "IsChuck"):
        _boolean_like(fields[name], False)
    provenance = {
        "status": "available",
        "filename": path.name,
        "sha256": _sha256_bytes(raw),
        "size": len(raw),
        "stage": project_stage(path),
        "gantry_rotary_config": {
            "axis": fields["Axis"],
            "axis_preserved_verbatim": True,
        },
    }
    return project, provenance


def _build_rotary_project(
    template: ET.Element,
    project_spec: RotaryProject,
) -> ET.Element:
    project = deepcopy(template)
    config = _rotary_config(project)
    mutations = {
        "Enabled": project_spec.enabled,
        "ObjectDiameter": project_spec.object_diameter_mm,
        "MirrorOutput": project_spec.mirror_output,
        "IsChuck": project_spec.is_chuck,
    }
    for name, value in mutations.items():
        if value is not None:
            _set_rotary_field(config, name, value)
    return project


def build_project(
    case: CapabilityCase,
    rotary_template: Path | None = None,
) -> ET.Element:
    """Build a LightBurn project without launching external software."""
    if isinstance(case.project, RasterProject):
        return _build_raster_project(case, case.project)
    if isinstance(case.project, VectorProject):
        return _build_vector_project(case.project)
    if rotary_template is None:
        raise ValueError("Rotary cases require a LightBurn-exported .lbrn2 template")
    template, _ = _parse_rotary_template(rotary_template)
    return _build_rotary_project(template, case.project)


def _serialize_project(
    case: CapabilityCase,
    rotary_template: ET.Element | None = None,
) -> bytes:
    if isinstance(case.project, RotaryProject):
        if rotary_template is None:
            raise ValueError("Rotary cases require a LightBurn-exported template")
        project = _build_rotary_project(rotary_template, case.project)
    else:
        project = build_project(case)
    ET.indent(project, space="    ")
    return ET.tostring(
        project,
        encoding="utf-8",
        xml_declaration=True,
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _load_json_object(path: Path, description: str) -> dict[str, object]:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid {description}: {error}") from error
    if not isinstance(document, dict):
        raise TypeError(f"{description} must be a JSON object")
    return document


def _canonical_json_bytes(document: object) -> bytes:
    content = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (content + "\n").encode("utf-8")


def _canonical_json_sha256(document: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(document))


def _json_pointer(parts: tuple[str | int, ...]) -> str:
    encoded = []
    for part in parts:
        value = str(part).replace("~", "~0").replace("/", "~1")
        encoded.append(value)
    return "/" + "/".join(encoded)


def _looks_like_local_path(value: str) -> bool:
    if value.startswith(("/", "~/", "file://", "\\\\")):
        return True
    return bool(PureWindowsPath(value).drive)


def _redaction(
    parts: tuple[str | int, ...],
    value: str,
    operation: Literal["remove", "replace"],
    reason: str,
) -> dict[str, object]:
    encoded = value.encode("utf-8")
    item: dict[str, object] = {
        "json_pointer": _json_pointer(parts),
        "operation": operation,
        "reason": reason,
        "original_type": "string",
        "original_utf8_size": len(encoded),
        "original_value_sha256": _sha256_bytes(encoded),
    }
    if operation == "replace":
        item["published_value"] = _REDACTED_PATH
    return item


def sanitize_profile_document(
    document: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Remove volatile local paths from a LightBurn profile document."""
    sanitized = deepcopy(document)
    redactions: list[dict[str, object]] = []

    def visit(value: object, parts: tuple[str | int, ...]) -> None:
        if isinstance(value, dict):
            for key in list(value):
                child = value[key]
                key_is_path = bool(_PATH_KEY.search(key))
                value_is_path = isinstance(child, str) and (
                    _looks_like_local_path(child)
                    or (key_is_path and "/" in child)
                    or (key_is_path and "\\" in child)
                    or (key.lower().startswith(("last", "recent")) and key_is_path)
                )
                if value_is_path and isinstance(child, str) and child:
                    reason = (
                        "volatile-path-setting"
                        if key_is_path
                        else "absolute-local-path"
                    )
                    redactions.append(
                        _redaction(parts + (key,), child, "remove", reason)
                    )
                    del value[key]
                    continue
                visit(child, parts + (key,))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, str) and _looks_like_local_path(child):
                    redactions.append(
                        _redaction(
                            parts + (index,),
                            child,
                            "replace",
                            "absolute-local-path",
                        )
                    )
                    value[index] = _REDACTED_PATH
                    continue
                visit(child, parts + (index,))

    visit(sanitized, ())
    return sanitized, redactions


def _device_profile_evidence(path: Path) -> dict[str, object]:
    document = _load_json_object(path, "LightBurn device profile")
    device_list = document.get("DeviceList")
    if not isinstance(device_list, list) or len(device_list) != 1:
        raise ValueError("LightBurn device profile must contain one DeviceList entry")
    device = device_list[0]
    if not isinstance(device, dict):
        raise TypeError("LightBurn DeviceList entry must be an object")
    controller_name = device.get("Name")
    if not isinstance(controller_name, str):
        raise TypeError("LightBurn DeviceList entry Name must be a string")
    if controller_name != "Ruida":
        raise ValueError(
            "LightBurn profile controller Name must be exactly 'Ruida'; "
            f"found {controller_name!r}"
        )
    profile_type = device.get("Type")
    if not isinstance(profile_type, str):
        raise TypeError("LightBurn DeviceList entry Type must be a string")
    settings = device.get("Settings")
    if not isinstance(settings, dict):
        raise TypeError("LightBurn device profile must contain Settings")
    profile_state = {
        requirement: settings.get(requirement)
        for requirement in (
            "EnableZ",
            "Laser1IsFiber",
            "Laser1IsRFTube",
            "Laser2Enabled",
            "SaveRotaryConfig",
        )
        if requirement in settings
    }
    return {
        "kind": "lightburn-device-profile",
        "filename": path.name,
        "evidence_sha256": _sha256(path),
        "document_sha256": _canonical_json_sha256(document),
        "size": path.stat().st_size,
        "display_name": device.get("DisplayName"),
        "controller_identity": {
            "field": "DeviceList[0].Name",
            "value": controller_name,
        },
        "profile_type": profile_type,
        "profile_state": profile_state,
    }


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _verified_profile_artifact(
    manifest_path: Path,
    filename: object,
    expected_sha256: str,
) -> Path:
    if not isinstance(filename, str):
        raise TypeError("Selected profile filename must be a string")
    relative = Path(filename)
    if relative.is_absolute() or relative.name != filename:
        raise ValueError("Selected profile filename must be a sibling name")
    artifact = manifest_path.parent / relative
    if not artifact.is_file():
        raise FileNotFoundError(artifact)
    if _sha256(artifact) != expected_sha256:
        raise ValueError(f"Selected profile hash does not match artifact: {filename}")
    return artifact


def _profile_matrix_evidence(
    path: Path,
    profile_variant: str | None,
) -> dict[str, object]:
    document = _load_json_object(path, "LightBurn profile matrix manifest")
    if document.get("schema") != "ruida-re.lightburn-profile-matrix":
        raise ValueError("Unsupported LightBurn profile matrix schema")
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported LightBurn profile matrix schema version")
    source = document.get("source")
    if not isinstance(source, dict):
        raise TypeError("Profile matrix source must be an object")
    controller_identity = source.get("controller_identity")
    if not isinstance(controller_identity, dict):
        raise TypeError("Profile matrix controller_identity must be an object")
    if controller_identity != {
        "field": "DeviceList[0].Name",
        "value": "Ruida",
    }:
        raise ValueError("Profile matrix does not identify a Ruida controller")
    profile_type = source.get("profile_type")
    if not isinstance(profile_type, str):
        raise TypeError("Profile matrix profile_type must be a string")
    source_sha256 = _require_sha256(
        source.get("source_sha256"),
        "Profile matrix source_sha256",
    )
    source_document_sha256 = _require_sha256(
        source.get("source_document_sha256"),
        "Profile matrix source_document_sha256",
    )
    if profile_variant is None:
        raise ValueError("A profile matrix requires an explicit profile variant")
    if profile_variant == "source":
        artifact = _verified_profile_artifact(
            path,
            source.get("filename"),
            source_sha256,
        )
        artifact_evidence = _device_profile_evidence(artifact)
        if artifact_evidence["document_sha256"] != source_document_sha256:
            raise ValueError(
                "Profile matrix source_document_sha256 does not match "
                "the selected source profile"
            )
        selected: dict[str, object] = {
            "identifier": "source",
            "profile_filename": artifact.name,
            "profile_sha256": source_sha256,
            "profile_state": artifact_evidence["profile_state"],
        }
    else:
        variants = document.get("variants")
        if not isinstance(variants, list):
            raise TypeError("Profile matrix variants must be an array")
        matches = [
            variant
            for variant in variants
            if isinstance(variant, dict)
            and variant.get("identifier") == profile_variant
        ]
        if len(matches) != 1:
            raise ValueError(f"Unknown or duplicate profile variant: {profile_variant}")
        variant = matches[0]
        changed_key = variant.get("changed_key")
        target_value = variant.get("target_value")
        variant_sha256 = _require_sha256(
            variant.get("variant_sha256"),
            "Profile variant hash",
        )
        if not isinstance(changed_key, str):
            raise TypeError("Profile variant changed_key must be a string")
        if not isinstance(target_value, bool):
            raise TypeError("Profile variant target_value must be boolean")
        artifact = _verified_profile_artifact(
            path,
            variant.get("filename"),
            variant_sha256,
        )
        artifact_evidence = _device_profile_evidence(artifact)
        artifact_state = artifact_evidence["profile_state"]
        if not isinstance(artifact_state, dict):
            raise TypeError("Selected profile state must be an object")
        if artifact_state.get(changed_key) is not target_value:
            raise ValueError("Selected profile does not match variant target state")
        selected = {
            "identifier": profile_variant,
            "profile_filename": artifact.name,
            "profile_sha256": variant_sha256,
            "profile_state": artifact_state,
        }
    if artifact_evidence["controller_identity"] != controller_identity:
        raise ValueError(
            "Selected profile controller identity differs from matrix source"
        )
    if artifact_evidence["profile_type"] != profile_type:
        raise ValueError("Selected profile type differs from matrix source")
    return {
        "kind": "lightburn-profile-matrix",
        "filename": path.name,
        "evidence_sha256": _sha256(path),
        "size": path.stat().st_size,
        "controller_identity": controller_identity,
        "profile_type": profile_type,
        **selected,
    }


def _capture_profile_evidence(
    path: Path,
    profile_variant: str | None,
) -> dict[str, object]:
    path = path.resolve()
    if path.suffix.lower() == ".lbdev":
        if profile_variant is not None:
            raise ValueError(
                "Profile variant applies only to a profile matrix manifest"
            )
        return _device_profile_evidence(path)
    return _profile_matrix_evidence(path, profile_variant)


def _validate_capture_profile(
    case: CapabilityCase,
    profile: dict[str, object],
) -> None:
    state = profile.get("profile_state")
    if not isinstance(state, dict):
        raise TypeError("Capture profile_state must be an object")
    missing = [
        requirement
        for requirement in case.profile_requirements
        if state.get(requirement) is not True
    ]
    if missing:
        raise ValueError(
            f"Profile evidence does not enable {', '.join(missing)} for "
            f"{case.identifier}"
        )


def _comparison(
    case: CapabilityCase,
    indexed: dict[str, CapabilityCase],
) -> dict[str, object] | None:
    if case.baseline is None:
        return None
    baseline = indexed[case.baseline]
    variable = case.independent_variable
    if variable is None:
        raise AssertionError(case.identifier)
    baseline_values = _project_values(baseline.project)
    case_values = _project_values(case.project)
    return {
        "baseline": case.baseline,
        "independent_variable": variable,
        "baseline_value": baseline_values[variable],
        "case_value": case_values[variable],
        "controlled_differences": sorted(controlled_differences(baseline, case)),
    }


def _case_manifest(
    case: CapabilityCase,
    directory: Path,
    indexed: dict[str, CapabilityCase],
    rotary_provenance: dict[str, object] | None,
    captures: dict[str, dict[str, object]],
) -> dict[str, object]:
    project_path = directory / f"{case.identifier}.lbrn2"
    rd_path = directory / f"{case.identifier}.rd"
    if isinstance(case.project, RasterProject):
        project_kind = "raster"
    elif isinstance(case.project, VectorProject):
        project_kind = "vector"
    else:
        project_kind = "rotary-template"
    rotary_blocked = (
        isinstance(case.project, RotaryProject) and rotary_provenance is None
    )
    capture = captures.get(case.identifier)
    files: dict[str, object] = {}
    item: dict[str, object] = {
        "identifier": case.identifier,
        "family": case.family,
        "purpose": case.purpose,
        "project": project_path.name,
        "expected_rd": rd_path.name,
        "fixture_status": "blocked" if rotary_blocked else "generated",
        "export_status": (
            "blocked"
            if rotary_blocked
            else "captured"
            if capture is not None
            else "pending"
        ),
        "blocked_reason": ROTARY_BLOCKED_REASON if rotary_blocked else None,
        "evidence": {
            "input_status": case.evidence_status,
            "protocol_interpretation": "pending",
            "hypothesis": case.hypothesis,
        },
        "profile_requirements": list(case.profile_requirements),
        "comparison": _comparison(case, indexed),
        "capture": capture,
        "controls": {
            "project_kind": project_kind,
            **_project_values(case.project),
        },
        "files": files,
    }
    if isinstance(case.project, RasterProject):
        png = encode_grayscale_png(case.project.pixels)
        item["bitmap"] = {
            "width_pixels": len(case.project.pixels[0]),
            "height_pixels": len(case.project.pixels),
            "embedded_png_sha256": _sha256_bytes(png),
        }
    paths = (
        ()
        if rotary_blocked
        else (project_path,)
        if capture is None
        else (project_path, rd_path)
    )
    for path in paths:
        if path.is_file():
            files[path.name] = {
                "sha256": _sha256(path),
                "size": path.stat().st_size,
                "stage": (
                    project_stage(path)
                    if path.suffix == ".lbrn2"
                    else "lightburn-machine-export"
                ),
            }
    return item


def _manifest(
    directory: Path,
    rotary_provenance: dict[str, object] | None = None,
    captures: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    indexed = {case.identifier: case for case in CASES}
    if captures is None:
        captures = {}
    captured_count = len(captures)
    available_case_count = sum(
        rotary_provenance is not None or not isinstance(case.project, RotaryProject)
        for case in CASES
    )
    if captured_count == 0:
        capture_status = "none"
    elif captured_count == available_case_count:
        capture_status = "complete"
    else:
        capture_status = "partial"
    return {
        "schema": "ruida-re-lightburn-capabilities-v1",
        "reference_software": {
            "name": "LightBurn",
            "version": "2.1.03",
            "platform": "macOS",
            "app_sha256": LIGHTBURN_APP_SHA256,
        },
        "machine": {
            "manufacturer": "Boss Laser",
            "model": "LS2040",
            "display_name": "Boss LS2040",
            "rotary_hardware_available": False,
        },
        "lightburn_profile": {
            "display_name": "Ruida 644XS",
            "controller": "Ruida",
            "connection": "Serial",
            "bed_width_mm": 991.1080322265625,
            "bed_height_mm": 599.947998046875,
            "mirror_x": True,
            "mirror_y": True,
        },
        "scope": {
            "project_generation": {
                "mode": "offline",
                "lightburn_launched": False,
                "hardware_contacted": False,
            },
            "export_capture": {
                "status": capture_status,
                "captured_case_count": captured_count,
                "available_case_count": available_case_count,
                "attestation_scope": "per-case",
            },
            "default_export_status": "pending",
        },
        "rotary_template": (
            rotary_provenance
            if rotary_provenance is not None
            else {
                "status": "required",
                "blocked_reason": ROTARY_BLOCKED_REASON,
            }
        ),
        "cases": [
            _case_manifest(
                case,
                directory,
                indexed,
                rotary_provenance,
                captures,
            )
            for case in CASES
        ],
    }


def _write_manifest(
    directory: Path,
    force: bool = False,
    rotary_provenance: dict[str, object] | None = None,
    captures: dict[str, dict[str, object]] | None = None,
) -> Path:
    path = directory / MANIFEST_NAME
    atomic_write_text(
        path,
        json.dumps(
            _manifest(directory, rotary_provenance, captures),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        force=force,
    )
    return path


def generate(
    directory: Path = CAPABILITY_DIR,
    force: bool = False,
    rotary_template: Path | None = None,
) -> None:
    """Write deterministic projects and a pending provenance manifest."""
    validate_cases()
    existing_exports = [
        directory / f"{case.identifier}.rd"
        for case in CASES
        if (directory / f"{case.identifier}.rd").is_file()
    ]
    if existing_exports:
        raise FileExistsError(
            "Refusing to regenerate projects beside existing RD export: "
            f"{existing_exports[0]}"
        )
    rotary_project: ET.Element | None = None
    rotary_provenance: dict[str, object] | None = None
    if rotary_template is not None:
        rotary_template = rotary_template.resolve()
        rotary_project, rotary_provenance = _parse_rotary_template(rotary_template)
    targets = [
        directory / f"{case.identifier}.lbrn2"
        for case in CASES
        if rotary_project is not None or not isinstance(case.project, RotaryProject)
    ]
    targets.append(directory / MANIFEST_NAME)
    if rotary_template is not None and any(
        path.resolve() == rotary_template for path in targets
    ):
        raise ValueError("Rotary output would overwrite its source template")
    for path in targets:
        if path.exists() and not force:
            raise FileExistsError(path)
    directory.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        if isinstance(case.project, RotaryProject) and rotary_project is None:
            continue
        atomic_write_bytes(
            directory / f"{case.identifier}.lbrn2",
            _serialize_project(case, rotary_project),
            force=force,
        )
    print(
        _write_manifest(
            directory,
            force=force,
            rotary_provenance=rotary_provenance,
        )
    )


def _recorded_manifest(directory: Path) -> dict[str, object] | None:
    path = directory / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid existing capability manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise TypeError("Existing capability manifest must be an object")
    return manifest


def _recorded_rotary_provenance(
    manifest: dict[str, object] | None,
) -> dict[str, object] | None:
    if manifest is None:
        return None
    provenance = manifest.get("rotary_template")
    if not isinstance(provenance, dict):
        return None
    if provenance.get("status") != "available":
        return None
    return provenance


def _recorded_captures(
    manifest: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if manifest is None:
        return {}
    items = manifest.get("cases")
    if not isinstance(items, list):
        return {}
    captures: dict[str, dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("Recorded capability case must be an object")
        identifier = item.get("identifier")
        capture = item.get("capture")
        if capture is None:
            continue
        if not isinstance(identifier, str):
            raise TypeError("Recorded capability identifier must be a string")
        if not isinstance(capture, dict):
            raise TypeError("Recorded capability capture must be an object")
        captures[identifier] = capture
    return captures


def _capture_profile_identity(capture: dict[str, object]) -> str:
    profile = capture.get("profile_evidence")
    if not isinstance(profile, dict):
        raise TypeError("Capture profile_evidence must be an object")
    selected_hash = profile.get(
        "profile_sha256",
        profile.get("evidence_sha256"),
    )
    selected_hash = _require_sha256(
        selected_hash,
        "Capture selected profile hash",
    )
    controller_identity = profile.get("controller_identity")
    if controller_identity != {
        "field": "DeviceList[0].Name",
        "value": "Ruida",
    }:
        raise ValueError("Capture profile does not identify Ruida")
    profile_type = profile.get("profile_type")
    if not isinstance(profile_type, str):
        raise TypeError("Capture profile_type must be a string")
    identity = {
        "kind": profile.get("kind"),
        "selected_profile_sha256": selected_hash,
        "controller_identity": controller_identity,
        "profile_type": profile_type,
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _attest_captures(
    directory: Path,
    rotary_provenance: dict[str, object] | None,
    recorded: dict[str, dict[str, object]],
    profile: dict[str, object] | None,
    attest_save_rd: bool,
) -> dict[str, dict[str, object]]:
    captures: dict[str, dict[str, object]] = {}
    for case in CASES:
        rd_path = directory / f"{case.identifier}.rd"
        if not rd_path.is_file():
            continue
        if isinstance(case.project, RotaryProject) and rotary_provenance is None:
            raise ValueError(
                f"Cannot attest rotary export without template provenance: "
                f"{case.identifier}"
            )
        project_path = directory / f"{case.identifier}.lbrn2"
        if not project_path.is_file():
            raise FileNotFoundError(project_path)
        rd_sha256 = _sha256(rd_path)
        project_sha256 = _sha256(project_path)
        previous = recorded.get(case.identifier)
        if previous is not None and previous.get("rd_sha256") == rd_sha256:
            if previous.get("project_sha256") != project_sha256:
                raise ValueError(
                    f"Stale RD export for changed project: {case.identifier}"
                )
            _capture_profile_identity(previous)
            recorded_profile = previous.get("profile_evidence")
            if not isinstance(recorded_profile, dict):
                raise TypeError("Recorded profile evidence must be an object")
            _validate_capture_profile(case, recorded_profile)
            if previous.get("profile_requirements") != list(case.profile_requirements):
                raise ValueError("Recorded profile requirements have changed")
            attestation = previous.get("export_attestation")
            if not isinstance(attestation, dict):
                raise TypeError("Recorded export attestation must be an object")
            if (
                attestation.get("machine_file_action") != "save-rd"
                or attestation.get("job_transmitted") is not False
            ):
                raise ValueError("Recorded Save RD attestation is invalid")
            captures[case.identifier] = previous
            continue
        if not attest_save_rd:
            raise ValueError(
                f"New or changed RD export requires explicit LightBurn "
                f"Save RD attestation: {case.identifier}"
            )
        if profile is None:
            raise ValueError(
                f"New or changed RD export requires profile evidence: {case.identifier}"
            )
        _validate_capture_profile(case, profile)
        captures[case.identifier] = {
            "rd_sha256": rd_sha256,
            "project_sha256": project_sha256,
            "profile_requirements": list(case.profile_requirements),
            "profile_evidence": profile,
            "export_attestation": {
                "machine_file_action": "save-rd",
                "lightburn_launched": True,
                "job_transmitted": False,
                "controller_connection": "not-attested",
            },
        }
    indexed = {case.identifier: case for case in CASES}
    for case in CASES:
        if case.baseline is None:
            continue
        baseline = indexed[case.baseline]
        baseline_capture = captures.get(baseline.identifier)
        case_capture = captures.get(case.identifier)
        if baseline_capture is None or case_capture is None:
            continue
        if _capture_profile_identity(baseline_capture) != _capture_profile_identity(
            case_capture
        ):
            raise ValueError(
                f"Profile evidence differs across controlled comparison: "
                f"{case.baseline} versus {case.identifier}"
            )
    return captures


def record(
    directory: Path = CAPABILITY_DIR,
    rotary_template: Path | None = None,
    profile_evidence: Path | None = None,
    profile_variant: str | None = None,
    attest_save_rd: bool = False,
) -> None:
    """Attest and hash available LightBurn RD exports incrementally."""
    validate_cases()
    manifest = _recorded_manifest(directory)
    recorded_rotary = _recorded_rotary_provenance(manifest)
    if rotary_template is None:
        rotary_provenance = recorded_rotary
    else:
        _, rotary_provenance = _parse_rotary_template(rotary_template.resolve())
        if recorded_rotary is not None and rotary_provenance != recorded_rotary:
            raise ValueError(
                "Rotary template differs from recorded generation provenance"
            )
    if profile_evidence is None:
        if profile_variant is not None:
            raise ValueError("Profile variant requires profile evidence")
        profile = None
    else:
        profile = _capture_profile_evidence(
            profile_evidence,
            profile_variant,
        )
    captures = _attest_captures(
        directory,
        rotary_provenance,
        _recorded_captures(manifest),
        profile,
        attest_save_rd,
    )
    print(
        _write_manifest(
            directory,
            force=True,
            rotary_provenance=rotary_provenance,
            captures=captures,
        )
    )


def _safe_named_source(root: Path, filename: object, label: str) -> Path:
    if not isinstance(filename, str) or not filename:
        raise TypeError(f"{label} filename must be a non-empty string")
    relative = Path(filename)
    if (
        relative.is_absolute()
        or PureWindowsPath(filename).drive
        or "\\" in filename
        or relative.name != filename
    ):
        raise ValueError(f"{label} filename must be a sibling name")
    root = root.resolve(strict=True)
    matches = []
    for candidate in root.rglob(filename):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"{label} escapes the source directory") from error
        if resolved not in matches:
            matches.append(resolved)
    if len(matches) != 1:
        raise ValueError(
            f"{label} must resolve to exactly one source artifact; found {len(matches)}"
        )
    return matches[0]


def _safe_case_source(root: Path, filename: object, label: str) -> Path:
    if not isinstance(filename, str) or not filename:
        raise TypeError(f"{label} filename must be a non-empty string")
    relative = Path(filename)
    if (
        relative.is_absolute()
        or PureWindowsPath(filename).drive
        or "\\" in filename
        or relative.name != filename
    ):
        raise ValueError(f"{label} filename must be a sibling name")
    target = (root.resolve(strict=True) / relative).resolve(strict=True)
    try:
        target.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ValueError(f"{label} escapes the source directory") from error
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def _case_file(
    source: Path,
    item: dict[str, object],
    filename: str,
    label: str,
) -> Path:
    files = item.get("files")
    if not isinstance(files, dict):
        raise TypeError(f"{label} files must be an object")
    metadata = files.get(filename)
    if not isinstance(metadata, dict):
        raise TypeError(f"{label} has no metadata for {filename}")
    expected_hash = _require_sha256(
        metadata.get("sha256"),
        f"{label} {filename} sha256",
    )
    path = _safe_case_source(source, filename, label)
    if _sha256(path) != expected_hash:
        raise ValueError(f"{label} artifact hash differs: {filename}")
    size = metadata.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise TypeError(f"{label} {filename} size must be an integer")
    if path.stat().st_size != size:
        raise ValueError(f"{label} artifact size differs: {filename}")
    return path


def _promotion_cases(
    source: Path,
    manifest: dict[str, object],
) -> list[dict[str, object]]:
    if manifest.get("schema") != "ruida-re-lightburn-capabilities-v1":
        raise ValueError("Unsupported capability fixture manifest schema")
    values = manifest.get("cases")
    if not isinstance(values, list):
        raise TypeError("Capability fixture cases must be an array")
    items: list[dict[str, object]] = []
    indexed: dict[str, dict[str, object]] = {}
    expected = {case.identifier: case for case in CASES}
    for value in values:
        if not isinstance(value, dict):
            raise TypeError("Capability fixture case must be an object")
        identifier = value.get("identifier")
        if not isinstance(identifier, str):
            raise TypeError("Capability fixture identifier must be a string")
        if identifier in indexed:
            raise ValueError(f"Duplicate capability case: {identifier}")
        indexed[identifier] = value
        items.append(value)
    if set(indexed) != set(expected):
        raise ValueError("Capability fixture case set differs from the generator")

    for identifier, case in expected.items():
        item = indexed[identifier]
        capture = item.get("capture")
        if isinstance(case.project, RotaryProject):
            if (
                item.get("fixture_status") != "blocked"
                or item.get("export_status") != "blocked"
                or capture is not None
                or item.get("files") != {}
            ):
                raise ValueError(f"Rotary case is not blocked: {identifier}")
            continue
        if item.get("export_status") != "captured":
            raise ValueError(f"Capability case is not captured: {identifier}")
        if not isinstance(capture, dict):
            raise TypeError(f"Capability capture must be an object: {identifier}")
        project_name = item.get("project")
        rd_name = item.get("expected_rd")
        if project_name != f"{identifier}.lbrn2":
            raise ValueError(f"Unexpected project filename: {identifier}")
        if rd_name != f"{identifier}.rd":
            raise ValueError(f"Unexpected RD filename: {identifier}")
        if not isinstance(project_name, str) or not isinstance(rd_name, str):
            raise TypeError(f"Capability filenames must be strings: {identifier}")
        project_path = _case_file(
            source,
            item,
            project_name,
            identifier,
        )
        rd_path = _case_file(source, item, rd_name, identifier)
        if capture.get("project_sha256") != _sha256(project_path):
            raise ValueError(f"Capture project hash differs: {identifier}")
        if capture.get("rd_sha256") != _sha256(rd_path):
            raise ValueError(f"Capture RD hash differs: {identifier}")
        if capture.get("profile_requirements") != list(case.profile_requirements):
            raise ValueError(f"Capture profile requirements differ: {identifier}")
        attestation = capture.get("export_attestation")
        if not isinstance(attestation, dict):
            raise TypeError(f"Missing export attestation: {identifier}")
        if (
            attestation.get("machine_file_action") != "save-rd"
            or attestation.get("job_transmitted") is not False
        ):
            raise ValueError(f"Invalid export attestation: {identifier}")
        _capture_profile_identity(capture)

    for case in CASES:
        if case.baseline is None or isinstance(case.project, RotaryProject):
            continue
        baseline_capture = indexed[case.baseline].get("capture")
        variant_capture = indexed[case.identifier].get("capture")
        if not isinstance(baseline_capture, dict) or not isinstance(
            variant_capture, dict
        ):
            raise TypeError("Controlled comparison capture must be an object")
        if _capture_profile_identity(baseline_capture) != _capture_profile_identity(
            variant_capture
        ):
            raise ValueError(
                f"Profile differs across controlled comparison: "
                f"{case.baseline} versus {case.identifier}"
            )
    return items


def _profile_artifact_publication(
    source: Path,
    filename: str,
) -> tuple[bytes, dict[str, object]]:
    source_path = _safe_named_source(
        source,
        filename,
        f"Profile {filename}",
    )
    document = _load_json_object(source_path, "LightBurn device profile")
    _device_profile_evidence(source_path)
    sanitized, redactions = sanitize_profile_document(document)
    published = _canonical_json_bytes(sanitized)
    metadata: dict[str, object] = {
        "capture_filename": filename,
        "path": (_PROFILE_DIRECTORY / filename).as_posix(),
        "original_sha256": _sha256(source_path),
        "original_document_sha256": _canonical_json_sha256(document),
        "original_size": source_path.stat().st_size,
        "published_sha256": _sha256_bytes(published),
        "published_document_sha256": _canonical_json_sha256(sanitized),
        "published_size": len(published),
        "redactions": redactions,
    }
    return published, metadata


def _matrix_profile_names(matrix: dict[str, object]) -> tuple[str, ...]:
    source = matrix.get("source")
    variants = matrix.get("variants")
    if not isinstance(source, dict):
        raise TypeError("Profile matrix source must be an object")
    if not isinstance(variants, list) or len(variants) != 5:
        raise ValueError("Profile matrix must contain five research variants")
    source_filename = source.get("filename")
    if not isinstance(source_filename, str):
        raise TypeError("Profile matrix source filename must be a string")
    names = [source_filename]
    identifiers = set()
    for item in variants:
        if not isinstance(item, dict):
            raise TypeError("Profile matrix variant must be an object")
        identifier = item.get("identifier")
        filename = item.get("filename")
        if not isinstance(identifier, str) or not isinstance(filename, str):
            raise TypeError("Profile matrix variant identity must be a string")
        identifiers.add(identifier)
        names.append(filename)
    expected_identifiers = {
        "enable-z",
        "laser-1-fiber",
        "laser-1-rf-tube",
        "laser-2-enabled",
        "save-rotary-config",
    }
    if identifiers != expected_identifiers or len(set(names)) != 6:
        raise ValueError("Unexpected LightBurn research profile matrix")
    return tuple(names)


def _matrix_rewrite(
    source: Path,
    cases: list[dict[str, object]],
) -> tuple[
    dict[str, bytes],
    dict[str, dict[str, object]],
    bytes,
    dict[str, object],
]:
    matrix_evidence = []
    direct_evidence = []
    for item in cases:
        capture = item.get("capture")
        if not isinstance(capture, dict):
            continue
        evidence = capture.get("profile_evidence")
        if not isinstance(evidence, dict):
            raise TypeError("Capture profile evidence must be an object")
        if evidence.get("kind") == "lightburn-profile-matrix":
            matrix_evidence.append(evidence)
        elif evidence.get("kind") == "lightburn-device-profile":
            direct_evidence.append(evidence)
        else:
            raise ValueError("Unsupported capture profile evidence kind")
    matrix_names = {item.get("filename") for item in matrix_evidence}
    if len(matrix_names) != 1:
        raise ValueError("Captures must reference exactly one profile matrix")
    matrix_name = next(iter(matrix_names))
    matrix_path = _safe_named_source(source, matrix_name, "Profile matrix")
    matrix = _load_json_object(matrix_path, "LightBurn profile matrix")
    if (
        matrix.get("schema") != "ruida-re.lightburn-profile-matrix"
        or matrix.get("schema_version") != 1
    ):
        raise ValueError("Unsupported LightBurn profile matrix")
    original_matrix_sha = _sha256(matrix_path)
    for evidence in matrix_evidence:
        if evidence.get("evidence_sha256") != original_matrix_sha:
            raise ValueError("Capture profile matrix hash differs")

    profile_names = _matrix_profile_names(matrix)
    direct_names = {item.get("filename") for item in direct_evidence}
    if direct_names != {profile_names[0]}:
        raise ValueError("Direct captures do not reference the matrix source profile")
    contents: dict[str, bytes] = {}
    publications: dict[str, dict[str, object]] = {}
    for filename in profile_names:
        content, publication = _profile_artifact_publication(source, filename)
        contents[filename] = content
        publications[filename] = publication

    published_matrix = deepcopy(matrix)
    rewrites: list[dict[str, object]] = []
    source_entry = published_matrix.get("source")
    if not isinstance(source_entry, dict):
        raise TypeError("Profile matrix source must be an object")
    source_publication = publications[profile_names[0]]
    source_updates = {
        "source_sha256": source_publication["published_sha256"],
        "source_document_sha256": source_publication["published_document_sha256"],
    }
    for key, published_value in source_updates.items():
        original_value = source_entry.get(key)
        expected_key = (
            "original_sha256" if key == "source_sha256" else "original_document_sha256"
        )
        if original_value != source_publication[expected_key]:
            raise ValueError(f"Profile matrix source {key} differs")
        source_entry[key] = published_value
        rewrites.append(
            {
                "json_pointer": f"/source/{key}",
                "operation": "replace",
                "original_value": original_value,
                "published_value": published_value,
            }
        )

    variants = published_matrix.get("variants")
    if not isinstance(variants, list):
        raise TypeError("Profile matrix variants must be an array")
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict):
            raise TypeError("Profile matrix variant must be an object")
        filename = variant.get("filename")
        if not isinstance(filename, str):
            raise TypeError("Profile matrix variant filename must be a string")
        publication = publications[filename]
        updates = {
            "variant_sha256": publication["published_sha256"],
            "size": publication["published_size"],
        }
        expected = {
            "variant_sha256": publication["original_sha256"],
            "size": publication["original_size"],
        }
        for key, published_value in updates.items():
            original_value = variant.get(key)
            if original_value != expected[key]:
                raise ValueError(f"Profile matrix variant {key} differs")
            variant[key] = published_value
            rewrites.append(
                {
                    "json_pointer": f"/variants/{index}/{key}",
                    "operation": "replace",
                    "original_value": original_value,
                    "published_value": published_value,
                }
            )

    matrix_content = _canonical_json_bytes(published_matrix)
    matrix_publication: dict[str, object] = {
        "capture_filename": matrix_path.name,
        "path": (_PROFILE_DIRECTORY / matrix_path.name).as_posix(),
        "original_sha256": original_matrix_sha,
        "original_document_sha256": _canonical_json_sha256(matrix),
        "original_size": matrix_path.stat().st_size,
        "published_sha256": _sha256_bytes(matrix_content),
        "published_document_sha256": _canonical_json_sha256(published_matrix),
        "published_size": len(matrix_content),
        "redactions": [],
        "rewrites": rewrites,
    }
    return contents, publications, matrix_content, matrix_publication


def _profile_publication_reference(
    publication: dict[str, object],
) -> dict[str, object]:
    return {
        "path": publication["path"],
        "original_sha256": publication["original_sha256"],
        "published_sha256": publication["published_sha256"],
        "redactions": publication["redactions"],
    }


def _published_profile_evidence(
    original: dict[str, object],
    profiles: dict[str, dict[str, object]],
    matrix: dict[str, object],
) -> dict[str, object]:
    published = deepcopy(original)
    kind = original.get("kind")
    filename = original.get("filename")
    if not isinstance(filename, str):
        raise TypeError("Profile evidence filename must be a string")
    if kind == "lightburn-device-profile":
        artifact = profiles.get(filename)
        if artifact is None:
            raise ValueError(f"Unknown direct profile artifact: {filename}")
        if original.get("evidence_sha256") != artifact["original_sha256"]:
            raise ValueError("Direct capture profile hash differs")
        if original.get("document_sha256") != artifact["original_document_sha256"]:
            raise ValueError("Direct capture profile document hash differs")
        published.update(
            {
                "filename": artifact["path"],
                "evidence_sha256": artifact["published_sha256"],
                "document_sha256": artifact["published_document_sha256"],
                "size": artifact["published_size"],
            }
        )
        publication: dict[str, object] = {
            "stage": "sanitized-publication",
            "profile": _profile_publication_reference(artifact),
        }
    elif kind == "lightburn-profile-matrix":
        selected_name = original.get("profile_filename")
        if not isinstance(selected_name, str):
            raise TypeError("Selected profile filename must be a string")
        artifact = profiles.get(selected_name)
        if artifact is None:
            raise ValueError(f"Unknown selected profile artifact: {selected_name}")
        if original.get("evidence_sha256") != matrix["original_sha256"]:
            raise ValueError("Capture profile matrix hash differs")
        if original.get("profile_sha256") != artifact["original_sha256"]:
            raise ValueError("Capture selected profile hash differs")
        published.update(
            {
                "filename": matrix["path"],
                "evidence_sha256": matrix["published_sha256"],
                "size": matrix["published_size"],
                "profile_filename": artifact["path"],
                "profile_sha256": artifact["published_sha256"],
            }
        )
        publication = {
            "stage": "sanitized-publication",
            "matrix": _profile_publication_reference(matrix),
            "selected_profile": _profile_publication_reference(artifact),
        }
    else:
        raise ValueError("Unsupported capture profile evidence kind")
    published["capture_origin"] = deepcopy(original)
    published["publication"] = publication
    return published


def _published_manifest(
    source: Path,
    original: dict[str, object],
    cases: list[dict[str, object]],
    profiles: dict[str, dict[str, object]],
    matrix: dict[str, object],
) -> dict[str, object]:
    published = deepcopy(original)
    published_cases = published.get("cases")
    if not isinstance(published_cases, list):
        raise TypeError("Published cases must be an array")
    for item in published_cases:
        if not isinstance(item, dict):
            raise TypeError("Published capability case must be an object")
        capture = item.get("capture")
        if capture is None:
            continue
        if not isinstance(capture, dict):
            raise TypeError("Published capture must be an object")
        evidence = capture.get("profile_evidence")
        if not isinstance(evidence, dict):
            raise TypeError("Published profile evidence must be an object")
        capture["profile_evidence"] = _published_profile_evidence(
            evidence,
            profiles,
            matrix,
        )
    captured_ids = [
        item["identifier"] for item in cases if item.get("export_status") == "captured"
    ]
    blocked_ids = [
        item["identifier"] for item in cases if item.get("family") == "rotary-candidate"
    ]
    published["publication"] = {
        "schema": PROMOTION_SCHEMA,
        "source_manifest_sha256": _sha256(source / MANIFEST_NAME),
        "capture_chain": "original capture hashes retained in capture_origin",
        "included_case_count": len(captured_ids),
        "included_case_ids": captured_ids,
        "included_families": list(PROMOTED_FAMILIES),
        "blocked_rotary_case_ids": blocked_ids,
        "excluded_artifacts": [
            "LightBurn backup files",
            "unattested or quarantined exports",
            "rotary template attempts",
            "blocked rotary case files",
        ],
        "profiles": [profiles[name] for name in sorted(profiles)],
        "profile_matrix": matrix,
    }
    return published


def _write_promotion_tree(
    source: Path,
    output: Path,
) -> None:
    from .experiment import (
        analyze,
        manifest_from_capability_fixture,
        manifest_json,
        report_json,
    )

    manifest_path = source / MANIFEST_NAME
    original = _load_json_object(manifest_path, "capability fixture manifest")
    cases = _promotion_cases(source, original)
    (
        profile_contents,
        profile_publications,
        matrix_content,
        matrix_publication,
    ) = _matrix_rewrite(source, cases)
    published = _published_manifest(
        source,
        original,
        cases,
        profile_publications,
        matrix_publication,
    )

    output.mkdir(parents=True, exist_ok=True)
    for item in cases:
        if item.get("export_status") != "captured":
            continue
        for field in ("project", "expected_rd"):
            filename = item.get(field)
            path = _safe_case_source(source, filename, "Capability artifact")
            atomic_write_bytes(output / path.name, path.read_bytes())
    profile_output = output / _PROFILE_DIRECTORY
    profile_output.mkdir(parents=True, exist_ok=True)
    for filename, content in profile_contents.items():
        atomic_write_bytes(profile_output / filename, content)
    matrix_name = matrix_publication["capture_filename"]
    if not isinstance(matrix_name, str):
        raise TypeError("Published matrix filename must be a string")
    atomic_write_bytes(profile_output / matrix_name, matrix_content)
    atomic_write_bytes(
        output / MANIFEST_NAME,
        _canonical_json_bytes(published),
    )

    for family in PROMOTED_FAMILIES:
        experiment = manifest_from_capability_fixture(published, family)
        experiment_path = output / f"{family}.experiment.json"
        atomic_write_text(experiment_path, manifest_json(experiment))
        report = analyze(experiment_path)
        report["manifest"] = experiment_path.name
        if report.get("valid") is not True:
            raise ValueError(f"Promoted experiment is invalid: {family}")
        atomic_write_text(
            output / f"{family}.report.json",
            report_json(report),
        )


def _relative_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _publish_tree(staged: Path, destination: Path, force: bool) -> None:
    if not destination.exists():
        staged.replace(destination)
        return
    if not destination.is_dir():
        raise FileExistsError(destination)
    if not force:
        raise FileExistsError(destination)
    staged_files = _relative_files(staged)
    destination_files = _relative_files(destination)
    if destination_files != staged_files:
        raise ValueError(
            "Existing publication file set differs; refusing a destructive update"
        )
    for relative in sorted(staged_files):
        atomic_write_bytes(
            destination / relative,
            (staged / relative).read_bytes(),
            force=True,
        )


def promote(
    source: Path,
    destination: Path,
    *,
    force: bool = False,
) -> Path:
    """Publish captured evidence as a sanitized, checked fixture tree."""
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if not source.is_dir():
        raise NotADirectoryError(source)
    if source == destination:
        raise ValueError("Promotion source and destination must differ")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("Promotion destination must not be inside its source")
    try:
        source.relative_to(destination)
    except ValueError:
        pass
    else:
        raise ValueError("Promotion source must not be inside its destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=destination.parent,
        prefix=f".{destination.name}-promotion-",
    ) as temporary:
        staged = Path(temporary) / destination.name
        _write_promotion_tree(source, staged)
        _publish_tree(staged, destination, force)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "record", "promote"))
    parser.add_argument("--directory", type=Path, default=CAPABILITY_DIR)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--rotary-template", type=Path)
    parser.add_argument("--profile-evidence", type=Path)
    parser.add_argument("--profile-variant")
    parser.add_argument(
        "--attest-lightburn-save-rd",
        action="store_true",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "generate":
        generate(
            args.directory,
            force=args.force,
            rotary_template=args.rotary_template,
        )
    elif args.action == "record":
        record(
            args.directory,
            rotary_template=args.rotary_template,
            profile_evidence=args.profile_evidence,
            profile_variant=args.profile_variant,
            attest_save_rd=args.attest_lightburn_save_rd,
        )
    else:
        if args.output_directory is None:
            parser.error("promote requires --output-directory")
        print(
            promote(
                args.directory,
                args.output_directory,
                force=args.force,
            )
        )


if __name__ == "__main__":
    main()
