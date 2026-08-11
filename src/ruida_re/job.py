"""Compile evidence-backed motion plans into complete Ruida jobs.

Advanced controls derived from offline producer fixtures remain isolated
behind explicit research profiles until controller execution is observed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal, TypeAlias

from .api import RuidaCodec
from .codec import encode_power
from .program import KnownCommand, Program

LayerKind = Literal["vector", "raster"]
ScanAxis = Literal["horizontal", "vertical"]
RasterStrategy = Literal["unidirectional", "bidirectional"]
RasterProcessingMode = Literal["native", "planned-path"]
DYNAMIC_POWER_RESTORE_CONTRACT = 1
MAX_ABSOLUTE_MM = 2_147_483.647
MAX_ABSOLUTE_MICRONS = 2_147_483_647
MAX_U35 = 34_359_738_367
S14_MIN = -(1 << 13)
S14_MAX = (1 << 13) - 1


class UnsupportedJobFeatureError(ValueError):
    """Raised when a plan requests an unprofiled Ruida feature."""


@dataclass(frozen=True)
class TravelTo:
    """Move to an absolute machine-space position without marking."""

    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class MarkTo:
    """Mark at layer power to an absolute machine-space position."""

    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class LaserChannelPlan:
    """One laser head's enabled state and effective power limits."""

    index: int
    enabled: bool
    min_power_percent: float
    max_power_percent: float


@dataclass(frozen=True)
class MarkWithPower:
    """Set persistent per-channel active powers, then mark a line."""

    x_mm: float
    y_mm: float
    laser_channels: tuple[LaserChannelPlan, ...]


@dataclass(frozen=True)
class MarkWithCurrentPower:
    """Mark with active powers left by a preceding MarkWithPower."""

    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class Dwell:
    """Wait at the current vector position without marking."""

    duration_ms: float


@dataclass(frozen=True)
class Pulse:
    """Mark at the current vector position for a fixed duration."""

    duration_ms: float


@dataclass(frozen=True)
class SetModulation:
    """Set 0..100 raster modulation, independent of layer power limits."""

    percent: float


LayerEvent: TypeAlias = (
    TravelTo
    | MarkTo
    | MarkWithPower
    | MarkWithCurrentPower
    | Dwell
    | Pulse
    | SetModulation
)


@dataclass(frozen=True)
class RasterSection:
    """One host-planned path block in a planned-path raster layer."""

    events: tuple[LayerEvent, ...]


@dataclass(frozen=True)
class LayerPlan:
    """One layer of planned motion without guessed controller features."""

    index: int
    speed_mm_s: float
    min_power_percent: float
    max_power_percent: float
    events: tuple[LayerEvent, ...]
    kind: LayerKind = "vector"
    air_assist: bool = False
    color_rgb: int = 0
    scan_axis: ScanAxis | None = None
    raster_strategy: RasterStrategy | None = None
    laser_index: int = 1
    raster_processing: RasterProcessingMode | None = None
    raster_sections: tuple[RasterSection, ...] = ()
    declared_metadata_bounds: Bounds | None = None
    laser_channels: tuple[LaserChannelPlan, ...] | None = None
    frequency_hz: int | None = None
    pulse_width_ns: int | None = None
    z_offset_mm: float | None = None


@dataclass(frozen=True)
class JobPlan:
    """An ordered set of layers in Ruida machine-space millimetres."""

    layers: tuple[LayerPlan, ...]
    reported_job_metric_mm: float | None = None
    declared_metadata_bounds: Bounds | None = None


@dataclass(frozen=True)
class Bounds:
    """Inclusive bounds of every planned motion endpoint."""

    min_x_mm: float
    min_y_mm: float
    max_x_mm: float
    max_y_mm: float

    @property
    def width_mm(self) -> float:
        return self.max_x_mm - self.min_x_mm

    @property
    def height_mm(self) -> float:
        return self.max_y_mm - self.min_y_mm


@dataclass(frozen=True)
class RasterMode:
    """One controlled raster scan mode in a producer profile."""

    scan_axis: ScanAxis
    strategy: RasterStrategy
    layer_mode: int
    layer_operation: int


@dataclass(frozen=True)
class PlannedPathRasterMode:
    """Observed planned-path raster envelope for one producer profile."""

    layer_mode: int
    layer_operation: int
    section_separator_operation: int
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"


@dataclass(frozen=True)
class LaserChannelMapping:
    """Profile-owned command and enable-bit mapping for one laser head."""

    index: int
    enable_mask: int
    layer_min_command: str
    layer_max_command: str
    active_min_command: str
    active_max_command: str


@dataclass(frozen=True)
class LaserChannelMode:
    """Observed two-head layer and active-power serialization."""

    mappings: tuple[LaserChannelMapping, ...]
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"
    allowed_enable_masks: tuple[int, ...] | None = None


@dataclass(frozen=True)
class StationaryEventMode:
    """Observed stationary wait and marking command mapping."""

    dwell_command: str
    pulse_command: str
    relative_travel_after_pulse: bool
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"
    max_duration_ms: float | None = None


@dataclass(frozen=True)
class LayerFrequencyMode:
    """Observed per-layer frequency selector sequence."""

    command: str
    selectors: tuple[int, ...]
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"
    minimum_hz: int | None = None
    maximum_hz: int | None = None


@dataclass(frozen=True)
class FiberPulseWidthMode:
    """Observed per-layer fiber pulse-width selector."""

    command: str
    selector_a: int
    selector_b: int
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"
    minimum_ns: int | None = None
    maximum_ns: int | None = None


@dataclass(frozen=True)
class PairedZOffsetMode:
    """Observed balanced Z-offset envelope around one native raster."""

    command: str
    enter_multiplier: int
    restore_multiplier: int
    section_operation: int
    axis_speed_mm_s: float
    layer_operation: int
    setup_operations: tuple[int, ...]
    laser_enable_value: int
    repeat_axis_speed_after_enter: bool
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"
    maximum_abs_offset_mm: float | None = None


@dataclass(frozen=True)
class DynamicVectorPowerMode:
    """Explicit active-power envelopes for stateful vector marking."""

    section_operation: int
    external_io_value: int
    semantic_evidence: str
    evidence_source: str
    execution_evidence: str = "not-observed"
    mutable_min_power_indices: tuple[int, ...] | None = None
    mutable_max_power_indices: tuple[int, ...] | None = None
    required_lower_max_power_indices: tuple[int, ...] | None = None


@dataclass(frozen=True)
class RuidaJobProfile:
    """Evidence-labelled literals for one observed job envelope.

    Execution evidence is independent of the controlled fixture evidence
    used to derive the envelope and command semantics.
    """

    identifier: str
    producer: str
    producer_version: str
    controller_profile: str
    envelope_evidence: str
    vector_semantic_evidence: str
    raster_semantic_evidence: str
    metric_semantic_evidence: str
    vector_layer_mode: int
    vector_layer_operation: int
    raster_modes: tuple[RasterMode, ...]
    air_off_operation: int
    air_on_operation: int
    laser_enable_value: int
    supported_laser_indices: tuple[int, ...]
    element_name: str
    execution_evidence: str = "not-observed"
    execution_evidence_source: str | None = None
    planned_path_raster_mode: PlannedPathRasterMode | None = None
    laser_channel_mode: LaserChannelMode | None = None
    stationary_event_mode: StationaryEventMode | None = None
    layer_frequency_mode: LayerFrequencyMode | None = None
    fiber_pulse_width_mode: FiberPulseWidthMode | None = None
    paired_z_offset_mode: PairedZOffsetMode | None = None
    dynamic_vector_power_mode: DynamicVectorPowerMode | None = None
    allowed_layer_kinds: tuple[LayerKind, ...] | None = None
    required_layer_count: int | None = None
    allowed_layer_indices: tuple[int, ...] | None = None
    required_raster_processing: RasterProcessingMode | None = None

    def mode_for(self, layer: LayerPlan) -> tuple[int, int]:
        """Return the observed metadata and program mode pair."""
        if layer.kind == "vector":
            return self.vector_layer_mode, self.vector_layer_operation
        if layer.raster_processing == "planned-path":
            if self.planned_path_raster_mode is None:
                raise UnsupportedJobFeatureError(
                    "Planned-path raster is not supported by this job profile"
                )
            return (
                self.planned_path_raster_mode.layer_mode,
                self.planned_path_raster_mode.layer_operation,
            )
        for mode in self.raster_modes:
            if (
                mode.scan_axis == layer.scan_axis
                and mode.strategy == layer.raster_strategy
            ):
                return mode.layer_mode, mode.layer_operation
        raise UnsupportedJobFeatureError(
            "Raster scan mode is not supported by this job profile"
        )

    def validate_laser(self, layer: LayerPlan) -> None:
        """Reject laser heads not established by controlled fixtures."""
        if (
            isinstance(layer.laser_index, bool)
            or not isinstance(layer.laser_index, int)
            or layer.laser_index not in self.supported_laser_indices
        ):
            raise UnsupportedJobFeatureError(
                f"Laser index {layer.laser_index!r} is not supported by "
                f"profile {self.identifier}"
            )


_PLANNED_PATH_RASTER_MODE = PlannedPathRasterMode(
    layer_mode=0,
    layer_operation=0,
    section_separator_operation=5,
    semantic_evidence="controlled-offline-fixture",
    evidence_source="LightBurn 2.1.03 capability fixtures c002 through c005",
)


LIGHTBURN_2103_644XS = RuidaJobProfile(
    identifier="lightburn-2.1.03-ruida-644xs",
    producer="LightBurn",
    producer_version="2.1.03",
    controller_profile="Ruida 644XS",
    envelope_evidence="fixture-observed",
    vector_semantic_evidence="controlled-fixture",
    raster_semantic_evidence="controlled-fixture",
    metric_semantic_evidence="controlled-fixture",
    vector_layer_mode=0,
    vector_layer_operation=0,
    raster_modes=(
        RasterMode("horizontal", "unidirectional", 1, 2),
        RasterMode("horizontal", "bidirectional", 2, 1),
        RasterMode("vertical", "unidirectional", 3, 4),
        RasterMode("vertical", "bidirectional", 4, 3),
    ),
    air_off_operation=0x12,
    air_on_operation=0x13,
    laser_enable_value=1,
    supported_laser_indices=(1,),
    element_name="554e4e414d454420",
    execution_evidence="operator-observed",
    execution_evidence_source=(
        "fixtures/hardware/ruida-644xs-usb-serial-v1/manifest-v1.json"
    ),
)


_LASER_CHANNEL_MODE = LaserChannelMode(
    mappings=(
        LaserChannelMapping(
            index=1,
            enable_mask=1,
            layer_min_command="layer_laser_1_min_power",
            layer_max_command="layer_laser_1_max_power",
            active_min_command="laser_1_min_power",
            active_max_command="laser_1_max_power",
        ),
        LaserChannelMapping(
            index=2,
            enable_mask=2,
            layer_min_command="layer_laser_2_min_power",
            layer_max_command="layer_laser_2_max_power",
            active_min_command="laser_2_min_power",
            active_max_command="laser_2_max_power",
        ),
    ),
    semantic_evidence="controlled-offline-fixture",
    evidence_source="LightBurn 2.1.03 capability fixtures c006 through c010",
    allowed_enable_masks=(1, 2, 3),
)

_SINGLE_LASER_CHANNEL_MODE = replace(
    _LASER_CHANNEL_MODE,
    allowed_enable_masks=(1,),
)

_STATIONARY_EVENT_MODE = StationaryEventMode(
    dwell_command="additional_delay",
    pulse_command="laser_interval",
    relative_travel_after_pulse=True,
    semantic_evidence="controlled-offline-fixture",
    evidence_source=(
        "LightBurn 2.1.03 capability fixtures c022 through c026 and "
        "c033 through c036"
    ),
    max_duration_ms=200,
)

_LAYER_FREQUENCY_MODE = LayerFrequencyMode(
    command="layer_frequency",
    selectors=(0, 1),
    semantic_evidence="controlled-offline-fixture",
    evidence_source="LightBurn 2.1.03 capability fixtures c037 through c039",
    minimum_hz=10_000,
    maximum_hz=20_000,
)

_FIBER_PULSE_WIDTH_MODE = FiberPulseWidthMode(
    command="layer_fiber_pulse_width",
    selector_a=0,
    selector_b=0,
    semantic_evidence="controlled-offline-fixture",
    evidence_source="LightBurn 2.1.03 capability fixtures c040 through c042",
    minimum_ns=0,
    maximum_ns=200,
)

_PAIRED_Z_OFFSET_MODE = PairedZOffsetMode(
    command="z_offset_delta",
    enter_multiplier=-1,
    restore_multiplier=1,
    section_operation=5,
    axis_speed_mm_s=15,
    layer_operation=0,
    setup_operations=(0x30, 0x10),
    laser_enable_value=3,
    repeat_axis_speed_after_enter=True,
    semantic_evidence="controlled-offline-fixture",
    evidence_source="LightBurn 2.1.03 capability fixtures c043 and c044",
    maximum_abs_offset_mm=1,
)

_DYNAMIC_VECTOR_POWER_MODE = DynamicVectorPowerMode(
    section_operation=5,
    external_io_value=0,
    semantic_evidence="controlled-offline-fixture",
    evidence_source=("LightBurn 2.1.03 capability fixtures c015 through c021"),
    mutable_min_power_indices=(),
    mutable_max_power_indices=(1,),
    required_lower_max_power_indices=(1,),
)


def _research_profile(
    suffix: str,
    **changes: object,
) -> RuidaJobProfile:
    return replace(
        LIGHTBURN_2103_644XS,
        identifier=f"{LIGHTBURN_2103_644XS.identifier}-{suffix}",
        execution_evidence="not-observed",
        execution_evidence_source=None,
        **changes,
    )


LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH = _research_profile(
    "planned-path-research",
    planned_path_raster_mode=_PLANNED_PATH_RASTER_MODE,
    allowed_layer_kinds=("raster",),
    required_layer_count=1,
    allowed_layer_indices=(0,),
    required_raster_processing="planned-path",
)
LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH = _research_profile(
    "dual-laser-research",
    laser_channel_mode=_LASER_CHANNEL_MODE,
    allowed_layer_kinds=("vector",),
    required_layer_count=1,
    allowed_layer_indices=(0,),
)
LIGHTBURN_2103_644XS_STATIONARY_RESEARCH = _research_profile(
    "stationary-research",
    laser_channel_mode=_SINGLE_LASER_CHANNEL_MODE,
    stationary_event_mode=_STATIONARY_EVENT_MODE,
    allowed_layer_kinds=("vector",),
    required_layer_count=1,
    allowed_layer_indices=(0,),
)
LIGHTBURN_2103_644XS_RF_RESEARCH = _research_profile(
    "rf-research",
    laser_channel_mode=_SINGLE_LASER_CHANNEL_MODE,
    layer_frequency_mode=_LAYER_FREQUENCY_MODE,
    allowed_layer_kinds=("vector",),
    required_layer_count=1,
    allowed_layer_indices=(0,),
)
LIGHTBURN_2103_644XS_FIBER_RESEARCH = _research_profile(
    "fiber-research",
    laser_channel_mode=_SINGLE_LASER_CHANNEL_MODE,
    fiber_pulse_width_mode=_FIBER_PULSE_WIDTH_MODE,
    allowed_layer_kinds=("vector",),
    required_layer_count=1,
    allowed_layer_indices=(0,),
)
LIGHTBURN_2103_644XS_Z_RESEARCH = _research_profile(
    "z-research",
    paired_z_offset_mode=_PAIRED_Z_OFFSET_MODE,
)
LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH = _research_profile(
    "dynamic-power-research",
    laser_channel_mode=_SINGLE_LASER_CHANNEL_MODE,
    dynamic_vector_power_mode=_DYNAMIC_VECTOR_POWER_MODE,
    allowed_layer_kinds=("vector",),
    required_layer_count=1,
    allowed_layer_indices=(0,),
)


@dataclass(frozen=True)
class CompileResult:
    """A complete generated Program and its derived plan information."""

    program: Program
    bounds: Bounds
    layer_bounds: tuple[Bounds, ...]
    metadata_bounds: Bounds
    metadata_layer_bounds: tuple[Bounds, ...]
    profile: RuidaJobProfile
    marked_distance_mm: float
    _codec: RuidaCodec = field(
        repr=False,
        compare=False,
        default_factory=RuidaCodec,
    )

    def encode_rd(self) -> bytes:
        """Encode a complete scrambled machine file."""
        return self._codec.encode(
            self.program,
            container="rd",
            checksum_policy="recompute",
        )

    def encode_logical(self) -> bytes:
        """Encode the complete unscrambled command stream."""
        return self._codec.encode(
            self.program,
            container="logical",
            checksum_policy="recompute",
        )

    def encode_datagrams(self, *, mtu: int = 1470) -> tuple[bytes, ...]:
        """Encode the complete job as ordered outbound datagrams."""
        return self._codec.encode_datagrams(
            self.program,
            mtu=mtu,
            checksum_policy="recompute",
        )


@dataclass(frozen=True)
class _PlanAnalysis:
    bounds: Bounds
    layer_bounds: tuple[Bounds, ...]
    metadata_bounds: Bounds
    metadata_layer_bounds: tuple[Bounds, ...]
    marked_distance_mm: float


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be a finite number")
    return normalized


def _integer(
    value: object,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _power(value: object, label: str) -> float:
    normalized = _number(value, label)
    if not 0 <= normalized <= 100:
        raise ValueError(f"{label} must be between 0 and 100")
    return normalized


def _duration_ms(value: object, label: str) -> float:
    duration = _number(value, label)
    encoded = round(duration * 1000)
    if not 1 <= encoded <= MAX_U35:
        raise ValueError(
            f"{label} must quantize between 1 microsecond and "
            f"{MAX_U35} microseconds"
        )
    return duration


def _validate_policy_indices(
    values: tuple[int, ...] | None,
    label: str,
    minimum: int = 0,
    maximum: int = 127,
    allow_empty: bool = False,
) -> None:
    if values is None:
        return
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ValueError(f"{label} must be a nonempty tuple")
    normalized = tuple(
        _integer(value, f"{label} entry", minimum, maximum)
        for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} entries must be unique")


def _validate_profile_policy(profile: RuidaJobProfile) -> None:
    if profile.allowed_layer_kinds is not None:
        kinds = profile.allowed_layer_kinds
        if not isinstance(kinds, tuple) or not kinds:
            raise ValueError("Allowed layer kinds must be a nonempty tuple")
        if any(kind not in ("vector", "raster") for kind in kinds):
            raise ValueError("Allowed layer kinds contain an unknown kind")
        if len(set(kinds)) != len(kinds):
            raise ValueError("Allowed layer kinds must be unique")
    if profile.required_layer_count is not None:
        _integer(
            profile.required_layer_count,
            "Required layer count",
            1,
            128,
        )
    _validate_policy_indices(
        profile.allowed_layer_indices,
        "Allowed layer indices",
    )
    if (
        profile.required_layer_count is not None
        and profile.allowed_layer_indices is not None
        and len(profile.allowed_layer_indices) < profile.required_layer_count
    ):
        raise ValueError(
            "Allowed layer indices cannot satisfy the required layer count"
        )
    if profile.required_raster_processing not in (
        None,
        "native",
        "planned-path",
    ):
        raise ValueError("Required raster processing mode is unknown")
    if (
        profile.required_raster_processing is not None
        and profile.allowed_layer_kinds is not None
        and "raster" not in profile.allowed_layer_kinds
    ):
        raise ValueError(
            "Required raster processing needs raster in allowed layer kinds"
        )
    if (
        profile.required_raster_processing == "planned-path"
        and profile.planned_path_raster_mode is None
    ):
        raise ValueError(
            "Required planned-path processing needs a planned-path mode"
        )

    channel_mode = profile.laser_channel_mode
    mapping_indices: tuple[int, ...] = ()
    possible_enable_masks = {0}
    if channel_mode is not None:
        mappings = channel_mode.mappings
        if not isinstance(mappings, tuple) or not mappings:
            raise ValueError("Laser channel mappings must be a nonempty tuple")
        normalized_indices = []
        combined_mask = 0
        for mapping in mappings:
            if not isinstance(mapping, LaserChannelMapping):
                raise ValueError(
                    "Laser channel mappings must contain mapping objects"
                )
            index = _integer(mapping.index, "Laser channel index", 1, 2)
            enable_mask = _integer(
                mapping.enable_mask,
                "Laser channel enable mask",
                1,
                127,
            )
            if combined_mask & enable_mask:
                raise ValueError("Laser channel enable masks cannot overlap")
            combined_mask |= enable_mask
            normalized_indices.append(index)
            possible_enable_masks.update(
                mask | enable_mask for mask in tuple(possible_enable_masks)
            )
        mapping_indices = tuple(normalized_indices)
        if mapping_indices != tuple(sorted(set(mapping_indices))):
            raise ValueError(
                "Laser channel mapping indices must be unique and increasing"
            )
    if (
        channel_mode is not None
        and channel_mode.allowed_enable_masks is not None
    ):
        masks = channel_mode.allowed_enable_masks
        if not isinstance(masks, tuple) or not masks:
            raise ValueError(
                "Allowed laser enable masks must be a nonempty tuple"
            )
        normalized_masks = tuple(
            _integer(mask, "Allowed laser enable mask", 1, 127)
            for mask in masks
        )
        if len(set(normalized_masks)) != len(normalized_masks):
            raise ValueError("Allowed laser enable masks must be unique")
        if any(mask not in possible_enable_masks for mask in normalized_masks):
            raise ValueError(
                "Allowed laser enable mask is not produced by the mappings"
            )

    stationary_mode = profile.stationary_event_mode
    if (
        stationary_mode is not None
        and stationary_mode.max_duration_ms is not None
    ):
        maximum = _number(
            stationary_mode.max_duration_ms,
            "Maximum stationary event duration",
        )
        if maximum <= 0:
            raise ValueError(
                "Maximum stationary event duration must be positive"
            )
        _duration_ms(maximum, "Maximum stationary event duration")

    frequency_mode = profile.layer_frequency_mode
    if frequency_mode is not None:
        minimum = frequency_mode.minimum_hz
        maximum = frequency_mode.maximum_hz
        if minimum is not None:
            _integer(minimum, "Minimum layer frequency", 1, MAX_U35)
        if maximum is not None:
            _integer(maximum, "Maximum layer frequency", 1, MAX_U35)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(
                "Minimum layer frequency cannot exceed maximum frequency"
            )

    pulse_width_mode = profile.fiber_pulse_width_mode
    if pulse_width_mode is not None:
        minimum = pulse_width_mode.minimum_ns
        maximum = pulse_width_mode.maximum_ns
        if minimum is not None:
            _integer(minimum, "Minimum fiber pulse width", 0, MAX_U35)
        if maximum is not None:
            _integer(maximum, "Maximum fiber pulse width", 0, MAX_U35)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(
                "Minimum fiber pulse width cannot exceed maximum pulse width"
            )

    z_mode = profile.paired_z_offset_mode
    if z_mode is not None and z_mode.maximum_abs_offset_mm is not None:
        maximum = _number(
            z_mode.maximum_abs_offset_mm,
            "Maximum absolute Z offset",
        )
        encoded_maximum = round(maximum * 1000)
        if not 1 <= encoded_maximum <= MAX_ABSOLUTE_MICRONS:
            raise ValueError(
                "Maximum absolute Z offset must fit the signed coordinate"
            )

    dynamic_mode = profile.dynamic_vector_power_mode
    if dynamic_mode is not None:
        if channel_mode is None:
            raise ValueError(
                "Dynamic vector power requires a laser channel mode"
            )
        _validate_policy_indices(
            dynamic_mode.mutable_min_power_indices,
            "Mutable minimum-power laser indices",
            1,
            2,
            True,
        )
        for indices in (
            dynamic_mode.mutable_min_power_indices,
            dynamic_mode.mutable_max_power_indices,
            dynamic_mode.required_lower_max_power_indices,
        ):
            if indices is not None and any(
                index not in mapping_indices for index in indices
            ):
                raise ValueError(
                    "Dynamic power policy references an unmapped laser"
                )
        required_lower = dynamic_mode.required_lower_max_power_indices
        mutable_maximums = dynamic_mode.mutable_max_power_indices
        if (
            required_lower is not None
            and mutable_maximums is not None
            and not set(required_lower).issubset(mutable_maximums)
        ):
            raise ValueError(
                "Required lower maximums must be mutable maximum channels"
            )
        _validate_policy_indices(
            dynamic_mode.mutable_max_power_indices,
            "Mutable maximum-power laser indices",
            1,
            2,
            True,
        )
        _validate_policy_indices(
            dynamic_mode.required_lower_max_power_indices,
            "Required lower maximum-power laser indices",
            1,
            2,
            True,
        )


def _wire_power_value(power_percent: float) -> int:
    encoded = encode_power(power_percent)
    return (encoded[0] << 7) | encoded[1]


def _wire_power_equal(first: float, second: float) -> bool:
    return _wire_power_value(first) == _wire_power_value(second)


def _channel_plans(
    channels: object,
    label: str,
) -> tuple[LaserChannelPlan, ...]:
    if not isinstance(channels, tuple) or not channels:
        raise ValueError(f"{label} must be a nonempty tuple")
    normalized = []
    previous_index = 0
    for channel in channels:
        if not isinstance(channel, LaserChannelPlan):
            raise ValueError(
                f"Every {label.lower()} entry must be a LaserChannelPlan"
            )
        index = _integer(channel.index, f"{label} index", 1, 2)
        if index <= previous_index:
            raise ValueError(f"{label} indices must be unique and increasing")
        if not isinstance(channel.enabled, bool):
            raise ValueError(f"{label} enabled state must be boolean")
        minimum = _power(
            channel.min_power_percent,
            f"{label} laser {index} minimum power",
        )
        maximum = _power(
            channel.max_power_percent,
            f"{label} laser {index} maximum power",
        )
        if minimum > maximum:
            raise ValueError(
                f"{label} laser {index} minimum power cannot exceed "
                "maximum power"
            )
        normalized.append(channel)
        previous_index = index
    return tuple(normalized)


def _coordinates(
    event: TravelTo | MarkTo | MarkWithPower | MarkWithCurrentPower,
) -> tuple[int, int]:
    coordinates = (
        (event.x_mm, "Motion X coordinate"),
        (event.y_mm, "Motion Y coordinate"),
    )
    normalized = []
    for value, label in coordinates:
        coordinate = _number(value, label)
        if not 0 <= coordinate <= MAX_ABSOLUTE_MM:
            raise ValueError(
                f"{label} must be between 0 and {MAX_ABSOLUTE_MM}"
            )
        microns = round(coordinate * 1000)
        if not 0 <= microns <= MAX_ABSOLUTE_MICRONS:
            raise ValueError(f"{label} must fit the absolute coordinate field")
        normalized.append(microns)
    return normalized[0], normalized[1]


def _bounds(points: list[tuple[int, int]]) -> Bounds:
    return Bounds(
        min(point[0] for point in points) / 1000,
        min(point[1] for point in points) / 1000,
        max(point[0] for point in points) / 1000,
        max(point[1] for point in points) / 1000,
    )


def _metadata_coordinate(value_mm: float) -> float:
    microns = round(value_mm * 1000)
    rounded_microns = (microns + 5) // 10 * 10
    if rounded_microns > MAX_ABSOLUTE_MICRONS:
        raise ValueError(
            "Metadata-rounded coordinate exceeds the absolute field"
        )
    return rounded_microns / 1000


def _metadata_bounds(bounds: Bounds) -> Bounds:
    return Bounds(
        _metadata_coordinate(bounds.min_x_mm),
        _metadata_coordinate(bounds.min_y_mm),
        _metadata_coordinate(bounds.max_x_mm),
        _metadata_coordinate(bounds.max_y_mm),
    )


def _normalize_declared_bounds(
    bounds: Bounds | None,
    label: str,
) -> Bounds | None:
    if bounds is None:
        return None
    if not isinstance(bounds, Bounds):
        raise ValueError(f"{label} must be Bounds")
    normalized = []
    for value, coordinate_label in (
        (bounds.min_x_mm, "minimum X"),
        (bounds.min_y_mm, "minimum Y"),
        (bounds.max_x_mm, "maximum X"),
        (bounds.max_y_mm, "maximum Y"),
    ):
        coordinate = _number(value, f"{label} {coordinate_label}")
        if not 0 <= coordinate <= MAX_ABSOLUTE_MM:
            raise ValueError(
                f"{label} {coordinate_label} must be between 0 and "
                f"{MAX_ABSOLUTE_MM}"
            )
        microns = round(coordinate * 1000)
        if not 0 <= microns <= MAX_ABSOLUTE_MICRONS:
            raise ValueError(
                f"{label} {coordinate_label} must fit the absolute "
                "coordinate field"
            )
        normalized.append(microns / 1000)
    result = Bounds(*normalized)
    if result.min_x_mm > result.max_x_mm or result.min_y_mm > result.max_y_mm:
        raise ValueError(f"{label} minimums cannot exceed maximums")
    return result


def _raster_mark_delta(
    layer: LayerPlan,
    position: tuple[int, int],
    target: tuple[int, int],
) -> int:
    dx = target[0] - position[0]
    dy = target[1] - position[1]
    if dx == 0 and dy == 0:
        return 0
    if layer.scan_axis == "horizontal" and dy == 0:
        return dx
    if layer.scan_axis == "vertical" and dx == 0:
        return dy
    raise UnsupportedJobFeatureError(
        "Raster marks must be axial along the declared scan axis"
    )


def _layer_event_groups(
    layer: LayerPlan,
) -> tuple[tuple[LayerEvent, ...], ...]:
    if not isinstance(layer.events, tuple):
        raise ValueError("Layer events must be a tuple")
    if not isinstance(layer.raster_sections, tuple):
        raise ValueError("Raster sections must be a tuple")

    if layer.kind == "vector":
        if (
            layer.scan_axis is not None
            or layer.raster_strategy is not None
            or layer.raster_processing is not None
            or layer.raster_sections
        ):
            raise ValueError(
                "Vector layers cannot declare raster processing settings"
            )
        if not layer.events:
            raise ValueError("Layer events must be a nonempty tuple")
        return (layer.events,)

    if layer.raster_processing not in (
        None,
        "native",
        "planned-path",
    ):
        raise UnsupportedJobFeatureError(
            "Raster layers require a supported processing mode"
        )
    if layer.raster_processing == "planned-path":
        if layer.scan_axis is not None or layer.raster_strategy is not None:
            raise ValueError(
                "Planned-path raster layers cannot declare native "
                "scan settings"
            )
        if layer.events:
            raise ValueError(
                "Planned-path raster motion must be grouped into "
                "raster sections"
            )
        if not layer.raster_sections:
            raise ValueError(
                "Planned-path raster layers require at least one "
                "raster section"
            )
        groups = []
        for section in layer.raster_sections:
            if not isinstance(section, RasterSection):
                raise ValueError(
                    "Every raster section must be a RasterSection"
                )
            if not isinstance(section.events, tuple) or not section.events:
                raise ValueError(
                    "Raster section events must be a nonempty tuple"
                )
            groups.append(section.events)
        return tuple(groups)

    if layer.raster_sections:
        raise ValueError("Native raster layers cannot declare raster sections")
    if layer.scan_axis not in ("horizontal", "vertical"):
        raise UnsupportedJobFeatureError(
            "Raster layers require a supported scan axis"
        )
    if layer.raster_strategy not in (
        "unidirectional",
        "bidirectional",
    ):
        raise UnsupportedJobFeatureError(
            "Raster layers require a supported scan strategy"
        )
    if not layer.events:
        raise ValueError("Layer events must be a nonempty tuple")
    return (layer.events,)


def _analyze_event_group(
    layer: LayerPlan,
    events: tuple[LayerEvent, ...],
) -> tuple[list[tuple[int, int]], list[float]]:
    points: list[tuple[int, int]] = []
    marked_distances = []
    position: tuple[int, int] | None = None
    saw_mark = False
    mark_sign: int | None = None
    pending_modulation = False
    dynamic_power_active = False

    for event in events:
        if isinstance(event, SetModulation):
            _power(event.percent, "Raster modulation")
            if layer.kind != "raster":
                raise UnsupportedJobFeatureError(
                    "SetModulation is only supported on raster layers"
                )
            if layer.raster_processing == "planned-path":
                raise UnsupportedJobFeatureError(
                    "SetModulation has no controlled planned-path "
                    "raster evidence"
                )
            pending_modulation = True
            continue
        if isinstance(event, (Dwell, Pulse)):
            _duration_ms(event.duration_ms, event.__class__.__name__)
            if layer.kind != "vector":
                raise UnsupportedJobFeatureError(
                    "Dwell and Pulse are only supported on vector layers"
                )
            if position is None:
                raise ValueError(
                    f"{event.__class__.__name__} requires a current position"
                )
            if isinstance(event, Pulse):
                saw_mark = True
            continue
        if isinstance(event, MarkWithPower):
            if layer.kind != "vector":
                raise UnsupportedJobFeatureError(
                    "MarkWithPower is only supported on vector layers"
                )
            _channel_plans(
                event.laser_channels,
                "MarkWithPower laser channels",
            )
            dynamic_power_active = True
        if isinstance(event, MarkWithCurrentPower):
            if layer.kind != "vector":
                raise UnsupportedJobFeatureError(
                    "MarkWithCurrentPower is only supported on vector "
                    "layers"
                )
            if not dynamic_power_active:
                raise ValueError(
                    "MarkWithCurrentPower requires active powers from a "
                    "preceding MarkWithPower"
                )
        if not isinstance(
            event,
            (TravelTo, MarkTo, MarkWithPower, MarkWithCurrentPower),
        ):
            raise ValueError(f"Unknown layer event: {event!r}")
        target = _coordinates(event)
        if position is None and not isinstance(event, TravelTo):
            raise ValueError(
                "The first positional event in a layer section must be "
                "TravelTo"
            )
        if isinstance(
            event,
            (MarkTo, MarkWithPower, MarkWithCurrentPower),
        ):
            if position is None:
                raise AssertionError("Mark position was not initialized")
            if target == position:
                raise ValueError(
                    f"{event.__class__.__name__} must have nonzero "
                    "wire-quantized length"
                )
            if (
                layer.kind == "raster"
                and layer.raster_processing != "planned-path"
            ):
                delta = _raster_mark_delta(layer, position, target)
                if delta != 0 and layer.raster_strategy == "unidirectional":
                    next_sign = 1 if delta > 0 else -1
                    if mark_sign is not None and next_sign != mark_sign:
                        raise UnsupportedJobFeatureError(
                            "Unidirectional raster marks must share one "
                            "direction"
                        )
                    mark_sign = next_sign
            marked_distances.append(math.dist(position, target) / 1000)
            pending_modulation = False
            saw_mark = True
            if isinstance(event, MarkTo):
                dynamic_power_active = False
        position = target
        points.append(target)

    if not points:
        raise ValueError("A layer section must contain positional events")
    if not saw_mark:
        raise ValueError(
            "A layer section must contain at least one marking event"
        )
    if pending_modulation:
        raise ValueError(
            "SetModulation must be followed by a MarkTo in its layer"
        )
    return points, marked_distances


def _analyze(plan: JobPlan) -> _PlanAnalysis:
    if not isinstance(plan, JobPlan):
        raise ValueError("Job plan must be a JobPlan")
    if not isinstance(plan.layers, tuple) or not plan.layers:
        raise ValueError("Job plan layers must be a nonempty tuple")
    if len(plan.layers) > 128:
        raise ValueError("A Ruida job can contain at most 128 layers")

    all_points: list[tuple[int, int]] = []
    layer_bounds = []
    metadata_layer_bounds = []
    marked_distances = []

    for expected_index, layer in enumerate(plan.layers):
        if not isinstance(layer, LayerPlan):
            raise ValueError("Every job layer must be a LayerPlan")
        index = _integer(layer.index, "Layer index", 0, 127)
        if index != expected_index:
            raise ValueError("Layer indices must be contiguous from zero")
        speed = _number(layer.speed_mm_s, "Layer speed")
        encoded_speed = round(speed * 1000)
        if not 1 <= encoded_speed <= MAX_U35:
            raise ValueError(
                "Layer speed must quantize into the scaled U35 field"
            )
        minimum = _power(layer.min_power_percent, "Layer minimum power")
        maximum = _power(layer.max_power_percent, "Layer maximum power")
        if minimum > maximum:
            raise ValueError("Layer minimum power cannot exceed maximum power")
        if layer.laser_channels is not None:
            channels = _channel_plans(
                layer.laser_channels,
                "Layer laser channels",
            )
            if not any(channel.enabled for channel in channels):
                raise ValueError(
                    "At least one explicit layer laser channel must be enabled"
                )
            if (
                isinstance(layer.laser_index, bool)
                or not isinstance(layer.laser_index, int)
                or layer.laser_index != 1
            ):
                raise ValueError(
                    "laser_index cannot be combined with explicit laser "
                    "channels"
                )
            first = channels[0]
            if first.index == 1 and (
                minimum
                != _power(
                    first.min_power_percent,
                    "Layer laser 1 minimum power",
                )
                or maximum
                != _power(
                    first.max_power_percent,
                    "Layer laser 1 maximum power",
                )
            ):
                raise ValueError(
                    "Legacy layer power limits must match explicit laser "
                    "1 power limits"
                )
        if layer.frequency_hz is not None:
            _integer(
                layer.frequency_hz,
                "Layer frequency",
                1,
                MAX_U35,
            )
        if layer.pulse_width_ns is not None:
            _integer(
                layer.pulse_width_ns,
                "Layer pulse width",
                0,
                MAX_U35,
            )
        if layer.z_offset_mm is not None:
            offset = _number(layer.z_offset_mm, "Layer Z offset")
            microns = round(offset * 1000)
            if not 1 <= abs(microns) <= MAX_ABSOLUTE_MICRONS:
                raise ValueError(
                    "Layer Z offset must quantize to a nonzero balanced "
                    "signed coordinate"
                )
        if layer.kind not in ("vector", "raster"):
            raise UnsupportedJobFeatureError(
                f"Unsupported layer kind: {layer.kind!r}"
            )
        event_groups = _layer_event_groups(layer)
        if not isinstance(layer.air_assist, bool):
            raise ValueError("Layer air assist must be boolean")
        _integer(layer.color_rgb, "Layer color", 0, 0xFFFFFF)

        points: list[tuple[int, int]] = []
        for events in event_groups:
            section_points, section_distances = _analyze_event_group(
                layer,
                events,
            )
            points.extend(section_points)
            marked_distances.extend(section_distances)
        current_bounds = _bounds(points)
        layer_bounds.append(current_bounds)
        declared_bounds = _normalize_declared_bounds(
            layer.declared_metadata_bounds,
            "Layer declared metadata bounds",
        )
        metadata_layer_bounds.append(
            declared_bounds or _metadata_bounds(current_bounds)
        )
        all_points.extend(points)

    derived_marked_distance = math.fsum(marked_distances)
    if plan.reported_job_metric_mm is None:
        marked_distance = derived_marked_distance
    else:
        marked_distance = _number(
            plan.reported_job_metric_mm,
            "Reported job metric",
        )
        if marked_distance < 0:
            raise ValueError("Reported job metric must be nonnegative")
    if not math.isfinite(marked_distance) or int(marked_distance) > MAX_U35:
        raise ValueError("Marked distance exceeds the Ruida job metric")

    bounds = _bounds(all_points)
    declared_bounds = _normalize_declared_bounds(
        plan.declared_metadata_bounds,
        "Job declared metadata bounds",
    )
    return _PlanAnalysis(
        bounds=bounds,
        layer_bounds=tuple(layer_bounds),
        metadata_bounds=declared_bounds or _metadata_bounds(bounds),
        metadata_layer_bounds=tuple(metadata_layer_bounds),
        marked_distance_mm=marked_distance,
    )


class RuidaJobCompiler:
    """Lower an emission-ready plan through one declarative envelope."""

    def __init__(
        self,
        profile: RuidaJobProfile = LIGHTBURN_2103_644XS,
        *,
        magic: int = 0x88,
    ) -> None:
        if not isinstance(profile, RuidaJobProfile):
            raise ValueError("Job profile must be a RuidaJobProfile")
        _validate_profile_policy(profile)
        self.profile = profile
        self.codec = RuidaCodec(magic=magic, context="job")

    def compile(self, plan: JobPlan) -> CompileResult:
        """Compile a validated plan into a complete checksummed Program."""
        analysis = _analyze(plan)
        self._validate_profile_features(plan)
        for layer in plan.layers:
            self.profile.mode_for(layer)

        records = self._prologue(analysis.metadata_bounds)
        for layer, bounds in zip(
            plan.layers,
            analysis.metadata_layer_bounds,
        ):
            records.extend(self._layer_metadata(layer, bounds))
        records.extend(self._document_metadata(plan, analysis))
        for layer in plan.layers:
            records.extend(self._layer_program(layer))
        records.extend(self._epilogue(analysis.marked_distance_mm))

        basis = sum(
            byte
            for record in records
            for byte in record.encode(self.codec._registry)
        )
        end = records.pop()
        records.extend(
            (
                self._command("file_checksum", value=basis),
                end,
            )
        )
        program = self.codec.program(records, container="rd")
        program = replace(program, source_checksum_basis=basis)
        self.codec.encode(program, checksum_policy="recompute")
        return CompileResult(
            program=program,
            bounds=analysis.bounds,
            layer_bounds=analysis.layer_bounds,
            metadata_bounds=analysis.metadata_bounds,
            metadata_layer_bounds=analysis.metadata_layer_bounds,
            profile=self.profile,
            marked_distance_mm=analysis.marked_distance_mm,
            _codec=self.codec,
        )

    def _command(self, name: str, **values: object) -> KnownCommand:
        return self.codec.command(name, **values)

    def _validate_profile_features(self, plan: JobPlan) -> None:
        self._validate_profile_scope(plan)
        z_layers = [
            layer for layer in plan.layers if layer.z_offset_mm is not None
        ]
        if z_layers:
            if self.profile.paired_z_offset_mode is None:
                raise UnsupportedJobFeatureError(
                    "Z offsets are not supported by this job profile"
                )
            if (
                len(plan.layers) != 1
                or len(z_layers) != 1
                or z_layers[0].kind != "raster"
                or z_layers[0].raster_processing == "planned-path"
            ):
                raise UnsupportedJobFeatureError(
                    "Z offsets require exactly one native raster layer"
                )
            z_mode = self.profile.paired_z_offset_mode
            if z_mode is None:
                raise AssertionError("Paired Z offset mode was not validated")
            if (
                z_mode.maximum_abs_offset_mm is not None
                and abs(z_layers[0].z_offset_mm)
                > z_mode.maximum_abs_offset_mm
            ):
                raise UnsupportedJobFeatureError(
                    "Z offset exceeds this job profile's absolute limit"
                )

        for layer in plan.layers:
            layer_channels = self._validate_laser_configuration(layer)
            if layer.frequency_hz is not None:
                if self.profile.layer_frequency_mode is None:
                    raise UnsupportedJobFeatureError(
                        "Layer frequency is not supported by this job profile"
                    )
                if layer.kind != "vector":
                    raise UnsupportedJobFeatureError(
                        "Layer frequency has controlled evidence only "
                        "for vector layers"
                    )
                frequency_mode = self.profile.layer_frequency_mode
                if frequency_mode is None:
                    raise AssertionError(
                        "Layer frequency mode was not validated"
                    )
                if (
                    frequency_mode.minimum_hz is not None
                    and layer.frequency_hz < frequency_mode.minimum_hz
                ) or (
                    frequency_mode.maximum_hz is not None
                    and layer.frequency_hz > frequency_mode.maximum_hz
                ):
                    raise UnsupportedJobFeatureError(
                        "Layer frequency is outside this job profile's limits"
                    )
            if layer.pulse_width_ns is not None:
                if self.profile.fiber_pulse_width_mode is None:
                    raise UnsupportedJobFeatureError(
                        "Fiber pulse width is not supported by this job "
                        "profile"
                    )
                if layer.kind != "vector":
                    raise UnsupportedJobFeatureError(
                        "Fiber pulse width has controlled evidence only "
                        "for vector layers"
                    )
                pulse_width_mode = self.profile.fiber_pulse_width_mode
                if pulse_width_mode is None:
                    raise AssertionError(
                        "Fiber pulse width mode was not validated"
                    )
                if (
                    pulse_width_mode.minimum_ns is not None
                    and layer.pulse_width_ns < pulse_width_mode.minimum_ns
                ) or (
                    pulse_width_mode.maximum_ns is not None
                    and layer.pulse_width_ns > pulse_width_mode.maximum_ns
                ):
                    raise UnsupportedJobFeatureError(
                        "Fiber pulse width is outside this job profile's "
                        "limits"
                    )

            for events in _layer_event_groups(layer):
                for event in events:
                    if isinstance(event, (Dwell, Pulse)) and (
                        self.profile.stationary_event_mode is None
                    ):
                        raise UnsupportedJobFeatureError(
                            "Stationary vector events are not supported "
                            "by this job profile"
                        )
                    if isinstance(event, (Dwell, Pulse)):
                        stationary_mode = self.profile.stationary_event_mode
                        if stationary_mode is None:
                            raise AssertionError(
                                "Stationary event mode was not validated"
                            )
                        if (
                            stationary_mode.max_duration_ms is not None
                            and event.duration_ms
                            > stationary_mode.max_duration_ms
                        ):
                            raise UnsupportedJobFeatureError(
                                "Stationary event duration exceeds this job "
                                "profile's limit"
                            )
                    if isinstance(event, MarkWithPower):
                        if self.profile.dynamic_vector_power_mode is None:
                            raise UnsupportedJobFeatureError(
                                "Dynamic vector power is not supported by "
                                "this job profile"
                            )
                        if layer_channels is None:
                            raise UnsupportedJobFeatureError(
                                "Dynamic vector power requires explicit "
                                "layer laser channels"
                            )
                        mark_channels = self._mapped_channels(
                            event.laser_channels,
                            "MarkWithPower laser channels",
                        )
                        if tuple(
                            (channel.index, channel.enabled)
                            for channel in mark_channels
                        ) != tuple(
                            (channel.index, channel.enabled)
                            for channel in layer_channels
                        ):
                            raise ValueError(
                                "MarkWithPower channel enable states must "
                                "match the layer"
                            )
                        self._validate_dynamic_power_limits(
                            layer_channels,
                            mark_channels,
                        )
                    if isinstance(event, MarkWithCurrentPower):
                        if self.profile.dynamic_vector_power_mode is None:
                            raise UnsupportedJobFeatureError(
                                "Current dynamic vector power is not "
                                "supported by this job profile"
                            )
                        if layer_channels is None:
                            raise UnsupportedJobFeatureError(
                                "Current dynamic vector power requires "
                                "explicit layer laser channels"
                            )

    def _validate_profile_scope(self, plan: JobPlan) -> None:
        required_count = self.profile.required_layer_count
        if required_count is not None and len(plan.layers) != required_count:
            raise UnsupportedJobFeatureError(
                f"Profile requires exactly {required_count} job layer(s)"
            )
        allowed_kinds = self.profile.allowed_layer_kinds
        if allowed_kinds is not None:
            for layer in plan.layers:
                if layer.kind not in allowed_kinds:
                    raise UnsupportedJobFeatureError(
                        f"Layer kind {layer.kind!r} is outside this job "
                        "profile's scope"
                    )
        allowed_indices = self.profile.allowed_layer_indices
        if allowed_indices is not None:
            for layer in plan.layers:
                if layer.index not in allowed_indices:
                    raise UnsupportedJobFeatureError(
                        f"Layer index {layer.index} is outside this job "
                        "profile's scope"
                    )
        required_processing = self.profile.required_raster_processing
        if required_processing is not None:
            for layer in plan.layers:
                actual_processing = layer.raster_processing or "native"
                if (
                    layer.kind != "raster"
                    or actual_processing != required_processing
                ):
                    raise UnsupportedJobFeatureError(
                        f"Profile requires {required_processing!r} raster "
                        "processing"
                    )

    def _validate_dynamic_power_limits(
        self,
        layer_channels: tuple[LaserChannelPlan, ...],
        mark_channels: tuple[LaserChannelPlan, ...],
    ) -> None:
        mode = self.profile.dynamic_vector_power_mode
        if mode is None:
            raise AssertionError("Dynamic vector power was not validated")
        mutable_minimums = mode.mutable_min_power_indices
        mutable_maximums = mode.mutable_max_power_indices
        required_lower_maximums = mode.required_lower_max_power_indices
        for layer_channel, mark_channel in zip(
            layer_channels,
            mark_channels,
        ):
            if (
                _wire_power_value(mark_channel.min_power_percent)
                < _wire_power_value(layer_channel.min_power_percent)
                or _wire_power_value(mark_channel.max_power_percent)
                > _wire_power_value(layer_channel.max_power_percent)
            ):
                raise UnsupportedJobFeatureError(
                    "MarkWithPower cannot exceed its layer channel limits"
                )
            if (
                mutable_minimums is not None
                and layer_channel.index not in mutable_minimums
                and not _wire_power_equal(
                    mark_channel.min_power_percent,
                    layer_channel.min_power_percent,
                )
            ):
                raise UnsupportedJobFeatureError(
                    "MarkWithPower changes a fixed minimum-power channel"
                )
            if (
                mutable_maximums is not None
                and layer_channel.index not in mutable_maximums
                and not _wire_power_equal(
                    mark_channel.max_power_percent,
                    layer_channel.max_power_percent,
                )
            ):
                raise UnsupportedJobFeatureError(
                    "MarkWithPower changes a fixed maximum-power channel"
                )
            if (
                required_lower_maximums is not None
                and layer_channel.index in required_lower_maximums
                and _wire_power_value(mark_channel.max_power_percent)
                >= _wire_power_value(layer_channel.max_power_percent)
            ):
                raise UnsupportedJobFeatureError(
                    "MarkWithPower must lower this channel's maximum power"
                )

    def _validate_laser_configuration(
        self,
        layer: LayerPlan,
    ) -> tuple[LaserChannelPlan, ...] | None:
        if layer.laser_channels is None:
            self.profile.validate_laser(layer)
            return None
        channels = self._mapped_channels(
            layer.laser_channels,
            "Layer laser channels",
        )
        mode = self.profile.laser_channel_mode
        if mode is None:
            raise AssertionError("Laser channel mode was not validated")
        enabled_mask = sum(
            mapping.enable_mask
            for mapping, channel in zip(mode.mappings, channels)
            if channel.enabled
        )
        if (
            mode.allowed_enable_masks is not None
            and enabled_mask not in mode.allowed_enable_masks
        ):
            raise UnsupportedJobFeatureError(
                f"Laser enable mask {enabled_mask} is outside this job "
                "profile's scope"
            )
        return channels

    def _mapped_channels(
        self,
        channels: tuple[LaserChannelPlan, ...],
        label: str,
    ) -> tuple[LaserChannelPlan, ...]:
        mode = self.profile.laser_channel_mode
        if mode is None:
            raise UnsupportedJobFeatureError(
                "Explicit laser channels are not supported by this job profile"
            )
        normalized = _channel_plans(channels, label)
        expected = tuple(mapping.index for mapping in mode.mappings)
        actual = tuple(channel.index for channel in normalized)
        if actual != expected:
            raise UnsupportedJobFeatureError(
                f"{label} must define profile laser heads {expected}"
            )
        return normalized

    def _point_command(
        self,
        name: str,
        bounds: Bounds,
        corner: Literal["min", "max"],
        **values: object,
    ) -> KnownCommand:
        if corner == "min":
            x_mm = bounds.min_x_mm
            y_mm = bounds.min_y_mm
        else:
            x_mm = bounds.max_x_mm
            y_mm = bounds.max_y_mm
        return self._command(name, **values, x_mm=x_mm, y_mm=y_mm)

    def _prologue(self, bounds: Bounds) -> list[KnownCommand]:
        return [
            self._command("reference_absolute"),
            self._command("set_absolute"),
            self._command("reference_point_set"),
            self._command("enable_block_cutting", enabled=0),
            self._command("process_start"),
            self._command("feed_repeat", first_value=0, second_value=0),
            self._command("feed_auto_pause", enabled=0),
            self._point_command("job_min_point", bounds, "min"),
            self._point_command("job_max_point", bounds, "max"),
            self._point_command("document_min_point", bounds, "min"),
            self._point_command("document_max_point", bounds, "max"),
            self._command(
                "job_copies",
                columns=1,
                rows=1,
                x_step_mm=0,
                y_step_mm=0,
            ),
            self._command("array_direction", direction=0),
        ]

    def _layer_metadata(
        self,
        layer: LayerPlan,
        bounds: Bounds,
    ) -> list[KnownCommand]:
        records = [
            self._command(
                "layer_speed",
                layer=layer.index,
                speed_mm_s=layer.speed_mm_s,
            ),
        ]
        if layer.frequency_hz is not None:
            mode = self.profile.layer_frequency_mode
            if mode is None:
                raise AssertionError("Layer frequency mode was not validated")
            records.extend(
                self._command(
                    mode.command,
                    laser=selector,
                    layer=layer.index,
                    frequency_khz=layer.frequency_hz / 1000,
                )
                for selector in mode.selectors
            )
        if layer.pulse_width_ns is not None:
            mode = self.profile.fiber_pulse_width_mode
            if mode is None:
                raise AssertionError(
                    "Fiber pulse width mode was not validated"
                )
            records.append(
                self._command(
                    mode.command,
                    selector_a=mode.selector_a,
                    selector_b=mode.selector_b,
                    pulse_width_ns=layer.pulse_width_ns,
                )
            )
        records.extend(self._layer_power_metadata(layer))
        records.extend(
            (
                self._command(
                    "layer_color",
                    layer=layer.index,
                    color_rgb=layer.color_rgb,
                ),
                self._command(
                    "layer_mode_or_attributes",
                    layer=layer.index,
                    value=self.profile.mode_for(layer)[0],
                ),
            )
        )
        metadata_points: tuple[tuple[str, Literal["min", "max"]], ...] = (
            ("layer_min_point", "min"),
            ("layer_max_point", "max"),
            ("layer_extended_min_point", "min"),
            ("layer_extended_max_point", "max"),
        )
        for name, corner in metadata_points:
            records.append(
                self._point_command(
                    name,
                    bounds,
                    corner,
                    layer=layer.index,
                )
            )
        return records

    def _layer_power_metadata(
        self,
        layer: LayerPlan,
    ) -> list[KnownCommand]:
        if layer.laser_channels is None:
            values = {
                "layer": layer.index,
                "power_percent": layer.min_power_percent,
            }
            return [
                self._command("layer_laser_1_min_power", **values),
                self._command(
                    "layer_laser_1_max_power",
                    layer=layer.index,
                    power_percent=layer.max_power_percent,
                ),
                self._command("layer_laser_2_min_power", **values),
                self._command(
                    "layer_laser_2_max_power",
                    layer=layer.index,
                    power_percent=layer.max_power_percent,
                ),
            ]

        mode = self.profile.laser_channel_mode
        if mode is None:
            raise AssertionError("Laser channel mode was not validated")
        records = []
        for mapping, channel in zip(mode.mappings, layer.laser_channels):
            records.extend(
                (
                    self._command(
                        mapping.layer_min_command,
                        layer=layer.index,
                        power_percent=channel.min_power_percent,
                    ),
                    self._command(
                        mapping.layer_max_command,
                        layer=layer.index,
                        power_percent=channel.max_power_percent,
                    ),
                )
            )
        return records

    def _document_metadata(
        self,
        plan: JobPlan,
        analysis: _PlanAnalysis,
    ) -> list[KnownCommand]:
        bounds = analysis.metadata_bounds
        return [
            self._command(
                "layer_count",
                count_minus_one=len(plan.layers) - 1,
            ),
            self._command("pen_offset_axis", axis=0, offset_mm=0),
            self._command("pen_offset_axis", axis=1, offset_mm=0),
            self._command("layer_offset_axis", axis=0, offset_mm=0),
            self._command("layer_offset_axis", axis=1, offset_mm=0),
            self._command("display_offset", x_mm=0, y_mm=0),
            self._command("element_max_index", index=0),
            self._command("element_name_max_index", index=0),
            self._command("element_index", index=0),
            self._command("element_name_index", index=0),
            self._command(
                "element_name",
                name_bytes=self.profile.element_name,
            ),
            self._point_command(
                "element_array_min_point",
                bounds,
                "min",
            ),
            self._point_command(
                "element_array_max_point",
                bounds,
                "max",
            ),
            self._command(
                "element_copies",
                columns=1,
                rows=1,
                x_step_mm=bounds.width_mm,
                y_step_mm=bounds.height_mm,
            ),
            self._command("element_array_add", x_mm=0, y_mm=0),
            self._command("element_array_mirror", value=0),
            self._command("array_start", value=0),
            self._command("current_element_index", index=0),
            self._point_command("array_min_point", bounds, "min"),
            self._point_command("array_max_point", bounds, "max"),
            self._point_command("array_add", bounds, "min"),
            self._command("array_mirror", value=0),
            self._point_command("array_even_distance", bounds, "max"),
            self._command(
                "array_copies",
                columns=1,
                rows=1,
                x_step_mm=bounds.width_mm,
                y_step_mm=bounds.height_mm,
            ),
        ]

    def _layer_program(self, layer: LayerPlan) -> list[KnownCommand]:
        if layer.raster_processing == "planned-path":
            mode = self.profile.planned_path_raster_mode
            if mode is None:
                raise UnsupportedJobFeatureError(
                    "Planned-path raster is not supported by this job profile"
                )
            records = []
            for index, section in enumerate(layer.raster_sections):
                if index:
                    records.append(
                        self._command(
                            "layer_control",
                            operation=mode.section_separator_operation,
                        )
                    )
                records.extend(
                    self._layer_section_program(
                        layer,
                        section.events,
                        mode.layer_operation,
                        raster_envelope=True,
                    )
                )
            return records

        records = self._layer_section_program(
            layer,
            layer.events,
            self.profile.mode_for(layer)[1],
            raster_envelope=layer.kind == "raster",
        )
        if layer.z_offset_mm is None:
            return records
        mode = self.profile.paired_z_offset_mode
        if mode is None:
            raise AssertionError("Paired Z offset mode was not validated")
        return [
            *self._z_offset_envelope(
                layer,
                layer.z_offset_mm * mode.enter_multiplier,
                entering=True,
            ),
            *records,
            *self._z_offset_envelope(
                layer,
                layer.z_offset_mm * mode.restore_multiplier,
                entering=False,
            ),
        ]

    def _z_offset_envelope(
        self,
        layer: LayerPlan,
        delta_mm: float,
        *,
        entering: bool,
    ) -> list[KnownCommand]:
        mode = self.profile.paired_z_offset_mode
        if mode is None:
            raise AssertionError("Paired Z offset mode was not validated")
        records = [
            self._command(
                "layer_control",
                operation=mode.section_operation,
            ),
            self._command(
                "axis_speed",
                speed_mm_s=mode.axis_speed_mm_s,
            ),
            self._command(
                "layer_control",
                operation=mode.layer_operation,
            ),
            self._command("select_layer", layer=layer.index),
            *(
                self._command("layer_control", operation=operation)
                for operation in mode.setup_operations
            ),
            self._command(
                "enable_laser_tube_start",
                enabled=mode.laser_enable_value,
            ),
            self._command(mode.command, delta_mm=delta_mm),
        ]
        if entering and mode.repeat_axis_speed_after_enter:
            records.append(
                self._command(
                    "axis_speed",
                    speed_mm_s=mode.axis_speed_mm_s,
                )
            )
        return records

    def _layer_section_program(
        self,
        layer: LayerPlan,
        events: tuple[LayerEvent, ...],
        layer_operation: int,
        *,
        raster_envelope: bool,
    ) -> list[KnownCommand]:
        air_operation = (
            self.profile.air_on_operation
            if layer.air_assist
            else self.profile.air_off_operation
        )
        records = [
            self._command("layer_control", operation=layer_operation),
            self._command("select_layer", layer=layer.index),
            self._command("layer_control", operation=0x30),
            self._command("layer_control", operation=0x10),
            self._command("layer_control", operation=air_operation),
            self._command("active_speed", speed_mm_s=layer.speed_mm_s),
        ]
        if not raster_envelope:
            records.extend(
                (
                    self._command("laser_on_delay", time_ms=0),
                    self._command("laser_off_delay", time_ms=0),
                    self._command(
                        "through_power_1",
                        power_percent=100 / 16383,
                    ),
                    self._command(
                        "through_power_2",
                        power_percent=100 / 16383,
                    ),
                )
            )
        records.extend(self._power_setup(layer))
        if raster_envelope:
            records.extend(
                (
                    self._command("laser_on_delay", time_ms=0),
                    self._command("laser_off_delay", time_ms=0),
                )
            )
        records.append(
            self._command(
                "enable_laser_tube_start",
                enabled=self._laser_enable_value(layer),
            )
        )

        position: tuple[int, int] | None = None
        previous_event: LayerEvent | None = None
        dynamic_power_active = False
        for event in events:
            if isinstance(event, SetModulation):
                records.extend(
                    (
                        self._command(
                            "immediate_power_1",
                            power_percent=event.percent,
                        ),
                        self._command(
                            "immediate_power_3",
                            power_percent=event.percent,
                        ),
                    )
                )
                previous_event = event
                continue
            if isinstance(event, Dwell):
                mode = self.profile.stationary_event_mode
                if mode is None:
                    raise AssertionError(
                        "Stationary event mode was not validated"
                    )
                records.append(
                    self._command(
                        mode.dwell_command,
                        time_ms=event.duration_ms,
                    )
                )
                previous_event = event
                continue
            if isinstance(event, Pulse):
                mode = self.profile.stationary_event_mode
                if mode is None:
                    raise AssertionError(
                        "Stationary event mode was not validated"
                    )
                records.append(
                    self._command(
                        mode.pulse_command,
                        time_ms=event.duration_ms,
                    )
                )
                previous_event = event
                continue
            if isinstance(event, MarkWithPower):
                records.extend(
                    self._dynamic_power_envelope(
                        layer,
                        event.laser_channels,
                    )
                )
                dynamic_power_active = True
            elif isinstance(event, MarkTo) and dynamic_power_active:
                if layer.laser_channels is None:
                    raise AssertionError(
                        "Dynamic vector power requires layer channels"
                    )
                records.extend(
                    self._dynamic_power_envelope(
                        layer,
                        layer.laser_channels,
                    )
                )
                dynamic_power_active = False

            target = _coordinates(event)
            if raster_envelope:
                records.append(self._raster_motion(event, position, target))
            elif isinstance(event, TravelTo):
                stationary_mode = self.profile.stationary_event_mode
                if (
                    stationary_mode is not None
                    and stationary_mode.relative_travel_after_pulse
                    and isinstance(previous_event, Pulse)
                ):
                    records.append(
                        self._raster_motion(event, position, target)
                    )
                else:
                    records.append(
                        self._command(
                            "move_absolute",
                            x_mm=target[0] / 1000,
                            y_mm=target[1] / 1000,
                        )
                    )
            else:
                records.append(
                    self._command(
                        "cut_absolute",
                        x_mm=target[0] / 1000,
                        y_mm=target[1] / 1000,
                    )
                )
            position = target
            previous_event = event
        if raster_envelope:
            records.append(self._command("block_end"))
        return records

    def _laser_enable_value(self, layer: LayerPlan) -> int:
        if layer.laser_channels is None:
            return self.profile.laser_enable_value
        mode = self.profile.laser_channel_mode
        if mode is None:
            raise AssertionError("Laser channel mode was not validated")
        return sum(
            mapping.enable_mask
            for mapping, channel in zip(
                mode.mappings,
                layer.laser_channels,
            )
            if channel.enabled
        )

    def _dynamic_power_envelope(
        self,
        layer: LayerPlan,
        laser_channels: tuple[LaserChannelPlan, ...],
    ) -> list[KnownCommand]:
        dynamic_mode = self.profile.dynamic_vector_power_mode
        channel_mode = self.profile.laser_channel_mode
        if dynamic_mode is None or channel_mode is None:
            raise AssertionError("Dynamic vector power was not validated")
        records = [
            self._command(
                "layer_control",
                operation=dynamic_mode.section_operation,
            ),
            self._command("select_layer", layer=layer.index),
        ]
        for mapping, channel in zip(
            channel_mode.mappings,
            laser_channels,
        ):
            records.extend(
                (
                    self._command(
                        mapping.active_min_command,
                        power_percent=channel.min_power_percent,
                    ),
                    self._command(
                        mapping.active_max_command,
                        power_percent=channel.max_power_percent,
                    ),
                )
            )
        records.append(
            self._command(
                "external_io",
                value=dynamic_mode.external_io_value,
            )
        )
        return records

    def _power_setup(self, layer: LayerPlan) -> list[KnownCommand]:
        if layer.laser_channels is not None:
            mode = self.profile.laser_channel_mode
            if mode is None:
                raise AssertionError("Laser channel mode was not validated")
            records = []
            for mapping, channel in zip(
                mode.mappings,
                layer.laser_channels,
            ):
                records.extend(
                    (
                        self._command(
                            mapping.active_min_command,
                            power_percent=channel.min_power_percent,
                        ),
                        self._command(
                            mapping.active_max_command,
                            power_percent=channel.max_power_percent,
                        ),
                    )
                )
            return records
        return [
            self._command(
                "laser_1_min_power",
                power_percent=layer.min_power_percent,
            ),
            self._command(
                "laser_1_max_power",
                power_percent=layer.max_power_percent,
            ),
            self._command(
                "laser_2_min_power",
                power_percent=layer.min_power_percent,
            ),
            self._command(
                "laser_2_max_power",
                power_percent=layer.max_power_percent,
            ),
        ]

    def _raster_motion(
        self,
        event: TravelTo | MarkTo | MarkWithPower | MarkWithCurrentPower,
        position: tuple[int, int] | None,
        target: tuple[int, int],
    ) -> KnownCommand:
        if position is None:
            return self._command(
                "move_absolute",
                x_mm=target[0] / 1000,
                y_mm=target[1] / 1000,
            )

        dx = target[0] - position[0]
        dy = target[1] - position[1]
        if isinstance(event, TravelTo):
            absolute_name = "move_absolute"
            relative_name = "move_relative"
            horizontal_name = "move_horizontal"
            vertical_name = "move_vertical"
        else:
            absolute_name = "cut_absolute"
            relative_name = "cut_relative"
            horizontal_name = "cut_horizontal"
            vertical_name = "cut_vertical"

        if dy == 0 and self._fits_s14(dx):
            return self._command(horizontal_name, dx_mm=dx / 1000)
        if dx == 0 and self._fits_s14(dy):
            return self._command(vertical_name, dy_mm=dy / 1000)
        if self._fits_s14(dx) and self._fits_s14(dy):
            return self._command(
                relative_name,
                dx_mm=dx / 1000,
                dy_mm=dy / 1000,
            )
        return self._command(
            absolute_name,
            x_mm=target[0] / 1000,
            y_mm=target[1] / 1000,
        )

    @staticmethod
    def _fits_s14(value: int) -> bool:
        return S14_MIN <= value <= S14_MAX

    def _epilogue(self, marked_distance: float) -> list[KnownCommand]:
        metric = int(marked_distance)
        return [
            self._command("array_end"),
            self._command("block_end"),
            self._command(
                "set_setting",
                address=800,
                first_value=metric,
                second_value=metric,
            ),
            self._command("end_of_file"),
        ]


__all__ = (
    "DYNAMIC_POWER_RESTORE_CONTRACT",
    "LIGHTBURN_2103_644XS",
    "LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH",
    "LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH",
    "LIGHTBURN_2103_644XS_FIBER_RESEARCH",
    "LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH",
    "LIGHTBURN_2103_644XS_RF_RESEARCH",
    "LIGHTBURN_2103_644XS_STATIONARY_RESEARCH",
    "LIGHTBURN_2103_644XS_Z_RESEARCH",
    "Bounds",
    "CompileResult",
    "Dwell",
    "DynamicVectorPowerMode",
    "FiberPulseWidthMode",
    "JobPlan",
    "LaserChannelMapping",
    "LaserChannelMode",
    "LaserChannelPlan",
    "LayerEvent",
    "LayerFrequencyMode",
    "LayerKind",
    "LayerPlan",
    "MarkTo",
    "MarkWithCurrentPower",
    "MarkWithPower",
    "PairedZOffsetMode",
    "PlannedPathRasterMode",
    "Pulse",
    "RasterMode",
    "RasterProcessingMode",
    "RasterSection",
    "RasterStrategy",
    "RuidaJobCompiler",
    "RuidaJobProfile",
    "ScanAxis",
    "SetModulation",
    "StationaryEventMode",
    "TravelTo",
    "UnsupportedJobFeatureError",
)
