"""Compile evidence-backed motion plans into complete Ruida jobs.

Frequency, dwell, and Z-axis job controls are intentionally absent until
controlled protocol evidence establishes their encoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Literal, TypeAlias

from .api import RuidaCodec
from .program import KnownCommand, Program


LayerKind = Literal["vector", "raster"]
ScanAxis = Literal["horizontal", "vertical"]
RasterStrategy = Literal["unidirectional", "bidirectional"]
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
    """Mark a line to an absolute machine-space position."""

    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class SetModulation:
    """Set 0..100 raster modulation, independent of layer power limits."""

    percent: float


LayerEvent: TypeAlias = TravelTo | MarkTo | SetModulation


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


@dataclass(frozen=True)
class JobPlan:
    """An ordered set of layers in Ruida machine-space millimetres."""

    layers: tuple[LayerPlan, ...]


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
class RuidaJobProfile:
    """Evidence-labelled literals for one observed job envelope."""

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

    def mode_for(self, layer: LayerPlan) -> tuple[int, int]:
        """Return the observed metadata and program mode pair."""
        if layer.kind == "vector":
            return self.vector_layer_mode, self.vector_layer_operation
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
        raise ValueError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return value


def _power(value: object, label: str) -> float:
    normalized = _number(value, label)
    if not 0 <= normalized <= 100:
        raise ValueError(f"{label} must be between 0 and 100")
    return normalized


def _coordinates(event: TravelTo | MarkTo) -> tuple[int, int]:
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
            raise ValueError(
                f"{label} must fit the absolute coordinate field"
            )
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
            raise ValueError(
                "Layer minimum power cannot exceed maximum power"
            )
        if layer.kind not in ("vector", "raster"):
            raise UnsupportedJobFeatureError(
                f"Unsupported layer kind: {layer.kind!r}"
            )
        if layer.kind == "vector":
            if (
                layer.scan_axis is not None
                or layer.raster_strategy is not None
            ):
                raise ValueError(
                    "Vector layers cannot declare raster scan settings"
                )
        else:
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
        if not isinstance(layer.air_assist, bool):
            raise ValueError("Layer air assist must be boolean")
        _integer(layer.color_rgb, "Layer color", 0, 0xFFFFFF)
        if not isinstance(layer.events, tuple) or not layer.events:
            raise ValueError("Layer events must be a nonempty tuple")

        points: list[tuple[int, int]] = []
        position: tuple[int, int] | None = None
        saw_mark = False
        mark_sign: int | None = None
        pending_modulation = False
        for event in layer.events:
            if isinstance(event, SetModulation):
                _power(event.percent, "Raster modulation")
                if layer.kind != "raster":
                    raise UnsupportedJobFeatureError(
                        "SetModulation is only supported on raster layers"
                    )
                pending_modulation = True
                continue
            if not isinstance(event, (TravelTo, MarkTo)):
                raise ValueError(f"Unknown layer event: {event!r}")
            target = _coordinates(event)
            if position is None and not isinstance(event, TravelTo):
                raise ValueError(
                    "The first positional event in a layer must be TravelTo"
                )
            if isinstance(event, MarkTo):
                if position is None:
                    raise AssertionError("Mark position was not initialized")
                if target == position:
                    raise ValueError(
                        "MarkTo must have nonzero wire-quantized length"
                    )
                if layer.kind == "raster":
                    delta = _raster_mark_delta(layer, position, target)
                    if (
                        delta != 0
                        and layer.raster_strategy == "unidirectional"
                    ):
                        next_sign = 1 if delta > 0 else -1
                        if mark_sign is not None and next_sign != mark_sign:
                            raise UnsupportedJobFeatureError(
                                "Unidirectional raster marks must share "
                                "one direction"
                            )
                        mark_sign = next_sign
                marked_distances.append(
                    math.dist(position, target) / 1000
                )
                pending_modulation = False
                saw_mark = True
            position = target
            points.append(target)
        if not points:
            raise ValueError("A layer must contain positional events")
        if not saw_mark:
            raise ValueError("A layer must contain at least one MarkTo")
        if pending_modulation:
            raise ValueError(
                "SetModulation must be followed by a MarkTo in its layer"
            )
        current_bounds = _bounds(points)
        layer_bounds.append(current_bounds)
        metadata_layer_bounds.append(_metadata_bounds(current_bounds))
        all_points.extend(points)

    marked_distance = math.fsum(marked_distances)
    if (
        not math.isfinite(marked_distance)
        or int(marked_distance) > MAX_U35
    ):
        raise ValueError("Marked distance exceeds the Ruida job metric")

    bounds = _bounds(all_points)
    return _PlanAnalysis(
        bounds=bounds,
        layer_bounds=tuple(layer_bounds),
        metadata_bounds=_metadata_bounds(bounds),
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
        self.profile = profile
        self.codec = RuidaCodec(magic=magic, context="job")

    def compile(self, plan: JobPlan) -> CompileResult:
        """Compile a validated plan into a complete checksummed Program."""
        analysis = _analyze(plan)
        for layer in plan.layers:
            self.profile.validate_laser(layer)
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
        values = {
            "layer": layer.index,
            "power_percent": layer.min_power_percent,
        }
        records = [
            self._command(
                "layer_speed",
                layer=layer.index,
                speed_mm_s=layer.speed_mm_s,
            ),
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
        ]
        for name, corner in (
            ("layer_min_point", "min"),
            ("layer_max_point", "max"),
            ("layer_extended_min_point", "min"),
            ("layer_extended_max_point", "max"),
        ):
            records.append(
                self._point_command(
                    name,
                    bounds,
                    corner,
                    layer=layer.index,
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
        air_operation = (
            self.profile.air_on_operation
            if layer.air_assist
            else self.profile.air_off_operation
        )
        layer_operation = self.profile.mode_for(layer)[1]
        records = [
            self._command("layer_control", operation=layer_operation),
            self._command("select_layer", layer=layer.index),
            self._command("layer_control", operation=0x30),
            self._command("layer_control", operation=0x10),
            self._command("layer_control", operation=air_operation),
            self._command("active_speed", speed_mm_s=layer.speed_mm_s),
        ]
        if layer.kind == "vector":
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
        if layer.kind == "raster":
            records.extend(
                (
                    self._command("laser_on_delay", time_ms=0),
                    self._command("laser_off_delay", time_ms=0),
                )
            )
        records.append(
            self._command(
                "enable_laser_tube_start",
                enabled=self.profile.laser_enable_value,
            )
        )

        position: tuple[int, int] | None = None
        for event in layer.events:
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
                continue

            target = _coordinates(event)
            if layer.kind == "raster":
                records.append(
                    self._raster_motion(event, position, target)
                )
            elif isinstance(event, TravelTo):
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
        if layer.kind == "raster":
            records.append(self._command("block_end"))
        return records

    def _power_setup(self, layer: LayerPlan) -> list[KnownCommand]:
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
        event: TravelTo | MarkTo,
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
    "Bounds",
    "CompileResult",
    "JobPlan",
    "LIGHTBURN_2103_644XS",
    "LayerEvent",
    "LayerKind",
    "LayerPlan",
    "MarkTo",
    "RasterMode",
    "RasterStrategy",
    "RuidaJobCompiler",
    "RuidaJobProfile",
    "ScanAxis",
    "SetModulation",
    "TravelTo",
    "UnsupportedJobFeatureError",
)
