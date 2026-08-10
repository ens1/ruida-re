"""Tests for emission-ready Ruida job compilation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest

from ruida_re.job import (
    Bounds,
    JobPlan,
    LIGHTBURN_2103_644XS,
    LayerPlan,
    MarkTo,
    RasterStrategy,
    RuidaJobCompiler,
    RuidaJobProfile,
    ScanAxis,
    SetModulation,
    TravelTo,
    UnsupportedJobFeatureError,
)
from ruida_re.program import KnownCommand


ROOT = Path(__file__).resolve().parents[1]
BASELINE = (
    ROOT
    / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"
)
MULTILAYER = (
    ROOT
    / "fixtures/lightburn-2.1.03/advanced/a001-multilayer.rd"
)
MIXED = (
    ROOT
    / "fixtures/lightburn-2.1.03/advanced/a004-mixed-vector-raster.rd"
)
RASTER = ROOT / "fixtures/lightburn-2.1.03/raster"
HORIZONTAL_UNIDIRECTIONAL = (
    RASTER / "r001-threshold-horizontal-unidirectional.rd"
)
GRAYSCALE_10_90 = RASTER / "r005-grayscale-range-10-90.rd"
VERTICAL_BIDIRECTIONAL = (
    RASTER / "r008-threshold-vertical-bidirectional.rd"
)


def _baseline_plan() -> JobPlan:
    return JobPlan(
        layers=(
            LayerPlan(
                index=0,
                speed_mm_s=10,
                min_power_percent=10,
                max_power_percent=20,
                events=(
                    TravelTo(20, 20),
                    MarkTo(30, 20),
                ),
            ),
        )
    )


def _raster_layer(
    events: tuple[TravelTo | MarkTo | SetModulation, ...],
    *,
    scan_axis: ScanAxis = "horizontal",
    raster_strategy: RasterStrategy = "unidirectional",
    min_power_percent: float = 50,
    max_power_percent: float = 50,
) -> LayerPlan:
    return LayerPlan(
        index=0,
        speed_mm_s=100,
        min_power_percent=min_power_percent,
        max_power_percent=max_power_percent,
        events=events,
        kind="raster",
        scan_axis=scan_axis,
        raster_strategy=raster_strategy,
    )


def _horizontal_unidirectional_plan() -> JobPlan:
    return JobPlan(
        layers=(
            _raster_layer(
                (
                    TravelTo(23.75, 21.75),
                    MarkTo(22.75, 21.75),
                    TravelTo(22.25, 21.75),
                    MarkTo(21.75, 21.75),
                    TravelTo(23.25, 21.25),
                    MarkTo(21.75, 21.25),
                    TravelTo(21.25, 21.25),
                    MarkTo(20.75, 21.25),
                    TravelTo(24.25, 20.75),
                    MarkTo(23.75, 20.75),
                    TravelTo(22.75, 20.75),
                    MarkTo(21.75, 20.75),
                    TravelTo(23.75, 20.25),
                    MarkTo(23.25, 20.25),
                    TravelTo(22.25, 20.25),
                    MarkTo(20.75, 20.25),
                )
            ),
        )
    )


def _grayscale_plan() -> JobPlan:
    modulation = (
        24.409448818897637,
        55.90551181102362,
        18.11023622047244,
        55.90551181102362,
        24.8062015503876,
        49.60629921259842,
        68.50393700787401,
    )
    events: list[TravelTo | MarkTo | SetModulation] = [
        TravelTo(23.5, 20.25)
    ]
    for index, percent in enumerate(modulation, start=1):
        events.extend(
            (
                SetModulation(percent),
                MarkTo(23.5 - index * 0.5, 20.25),
            )
        )
    return JobPlan(
        layers=(
            _raster_layer(
                tuple(events),
                min_power_percent=10,
                max_power_percent=90,
            ),
        )
    )


def _vertical_bidirectional_plan() -> JobPlan:
    return JobPlan(
        layers=(
            _raster_layer(
                (
                    TravelTo(21.25, 21.75),
                    MarkTo(21.25, 21.25),
                    TravelTo(21.25, 20.75),
                    MarkTo(21.25, 20.25),
                    TravelTo(21.75, 20.25),
                    MarkTo(21.75, 20.75),
                    TravelTo(22.25, 22.25),
                    MarkTo(22.25, 20.25),
                    TravelTo(22.75, 20.75),
                    MarkTo(22.75, 21.75),
                    TravelTo(23.25, 22.25),
                    MarkTo(23.25, 21.25),
                ),
                scan_axis="vertical",
                raster_strategy="bidirectional",
            ),
        )
    )


def _mixed_plan() -> JobPlan:
    return JobPlan(
        layers=(
            LayerPlan(
                index=0,
                speed_mm_s=10,
                min_power_percent=10,
                max_power_percent=20,
                events=(TravelTo(20, 26), MarkTo(30, 26)),
            ),
            LayerPlan(
                index=1,
                speed_mm_s=100,
                min_power_percent=50,
                max_power_percent=50,
                events=(
                    TravelTo(29.75, 21.75),
                    MarkTo(28.75, 21.75),
                    TravelTo(28.25, 21.75),
                    MarkTo(27.75, 21.75),
                    TravelTo(29.25, 21.25),
                    MarkTo(27.75, 21.25),
                    TravelTo(27.25, 21.25),
                    MarkTo(26.75, 21.25),
                    TravelTo(30.25, 20.75),
                    MarkTo(29.75, 20.75),
                    TravelTo(28.75, 20.75),
                    MarkTo(27.75, 20.75),
                    TravelTo(29.75, 20.25),
                    MarkTo(29.25, 20.25),
                    TravelTo(28.25, 20.25),
                    MarkTo(26.75, 20.25),
                ),
                kind="raster",
                color_rgb=0x0000FF,
                scan_axis="horizontal",
                raster_strategy="unidirectional",
            ),
        )
    )


class RuidaJobCompilerTest(unittest.TestCase):
    def test_baseline_is_exact_lightburn_machine_file(self) -> None:
        result = RuidaJobCompiler().compile(_baseline_plan())

        self.assertEqual(result.encode_rd(), BASELINE.read_bytes())
        self.assertEqual(len(result.program.records), 70)
        self.assertEqual(
            result.bounds,
            Bounds(20.0, 20.0, 30.0, 20.0),
        )
        self.assertEqual(result.marked_distance_mm, 10.0)
        self.assertEqual(
            result.metadata_bounds,
            Bounds(20.0, 20.0, 30.0, 20.0),
        )

        checksum = next(
            record.values["value"]
            for record in result.program.records
            if isinstance(record, KnownCommand)
            and record.name == "file_checksum"
        )
        self.assertEqual(checksum, result.program.source_checksum_basis)
        self.assertEqual(
            result._codec.encode(result.program),
            result.encode_rd(),
        )

    def test_multilayer_is_exact_lightburn_machine_file(self) -> None:
        plan = JobPlan(
            layers=(
                LayerPlan(
                    index=0,
                    speed_mm_s=10,
                    min_power_percent=10,
                    max_power_percent=20,
                    events=(
                        TravelTo(20, 30),
                        MarkTo(30, 30),
                    ),
                ),
                LayerPlan(
                    index=1,
                    speed_mm_s=15,
                    min_power_percent=30,
                    max_power_percent=40,
                    events=(
                        TravelTo(30, 20),
                        MarkTo(20, 20),
                    ),
                    air_assist=True,
                    color_rgb=0x0000FF,
                ),
            )
        )

        result = RuidaJobCompiler().compile(plan)

        self.assertEqual(result.encode_rd(), MULTILAYER.read_bytes())
        self.assertEqual(len(result.program.records), 98)
        self.assertEqual(
            result.layer_bounds,
            (
                Bounds(20.0, 30.0, 30.0, 30.0),
                Bounds(20.0, 20.0, 30.0, 20.0),
            ),
        )
        self.assertEqual(result.bounds, Bounds(20.0, 20.0, 30.0, 30.0))
        self.assertEqual(result.marked_distance_mm, 20.0)

    def test_horizontal_raster_is_exact_lightburn_file(self) -> None:
        result = RuidaJobCompiler().compile(
            _horizontal_unidirectional_plan()
        )

        self.assertEqual(
            result.encode_rd(),
            HORIZONTAL_UNIDIRECTIONAL.read_bytes(),
        )
        self.assertEqual(
            result.bounds,
            Bounds(20.75, 20.25, 24.25, 21.75),
        )
        self.assertEqual(result.marked_distance_mm, 7.0)

    def test_grayscale_raster_is_exact_lightburn_file(self) -> None:
        result = RuidaJobCompiler().compile(_grayscale_plan())

        self.assertEqual(result.encode_rd(), GRAYSCALE_10_90.read_bytes())
        names = [
            record.name
            for record in result.program.records
            if isinstance(record, KnownCommand)
        ]
        first = names.index("immediate_power_1")
        self.assertEqual(
            names[first : first + 3],
            [
                "immediate_power_1",
                "immediate_power_3",
                "cut_horizontal",
            ],
        )
        self.assertEqual(result.marked_distance_mm, 3.5)
        metric = next(
            record.values
            for record in result.program.records
            if isinstance(record, KnownCommand)
            and record.name == "set_setting"
        )
        self.assertEqual(metric["first_value"], 3)
        self.assertEqual(metric["second_value"], 3)

    def test_vertical_bidirectional_is_exact_lightburn_file(self) -> None:
        result = RuidaJobCompiler().compile(
            _vertical_bidirectional_plan()
        )

        self.assertEqual(
            result.encode_rd(),
            VERTICAL_BIDIRECTIONAL.read_bytes(),
        )
        self.assertEqual(result.marked_distance_mm, 5.5)

    def test_mixed_vector_raster_is_exact_lightburn_file(self) -> None:
        result = RuidaJobCompiler().compile(_mixed_plan())

        self.assertEqual(result.encode_rd(), MIXED.read_bytes())
        self.assertEqual(len(result.program.records), 111)
        self.assertEqual(
            result.layer_bounds,
            (
                Bounds(20.0, 26.0, 30.0, 26.0),
                Bounds(26.75, 20.25, 30.25, 21.75),
            ),
        )

    def test_explicit_modulation_events_are_preserved(self) -> None:
        plan = JobPlan(
            layers=(
                _raster_layer(
                    (
                        TravelTo(1, 1),
                        SetModulation(50),
                        SetModulation(50),
                        MarkTo(2, 1),
                    )
                ),
            )
        )

        result = RuidaJobCompiler().compile(plan)
        names = [
            record.name
            for record in result.program.records
            if isinstance(record, KnownCommand)
        ]
        first = names.index("move_absolute")
        self.assertEqual(
            names[first : first + 7],
            [
                "move_absolute",
                "immediate_power_1",
                "immediate_power_3",
                "immediate_power_1",
                "immediate_power_3",
                "cut_horizontal",
                "block_end",
            ],
        )

    def test_raster_marks_must_follow_declared_scan_axis(self) -> None:
        layers = (
            _raster_layer((TravelTo(0, 0), MarkTo(0, 1))),
            _raster_layer((TravelTo(0, 0), MarkTo(1, 1))),
            _raster_layer(
                (TravelTo(0, 0), MarkTo(1, 0)),
                scan_axis="vertical",
            ),
        )

        for layer in layers:
            with self.subTest(layer=layer):
                with self.assertRaisesRegex(
                    UnsupportedJobFeatureError,
                    "declared scan axis",
                ):
                    RuidaJobCompiler().compile(JobPlan((layer,)))

    def test_unidirectional_raster_marks_share_one_sign(self) -> None:
        events = (
            TravelTo(0, 0),
            MarkTo(1, 0),
            TravelTo(2, 1),
            MarkTo(1, 1),
        )
        unidirectional = JobPlan(
            layers=(_raster_layer(events),)
        )
        bidirectional = JobPlan(
            layers=(
                _raster_layer(
                    events,
                    raster_strategy="bidirectional",
                ),
            )
        )

        with self.assertRaisesRegex(
            UnsupportedJobFeatureError,
            "share one direction",
        ):
            RuidaJobCompiler().compile(unidirectional)
        RuidaJobCompiler().compile(bidirectional)

    def test_modulation_requires_a_later_mark(self) -> None:
        invalid = JobPlan(
            layers=(
                _raster_layer(
                    (
                        TravelTo(0, 0),
                        MarkTo(1, 0),
                        SetModulation(50),
                        TravelTo(2, 0),
                    )
                ),
            )
        )
        valid = JobPlan(
            layers=(
                _raster_layer(
                    (
                        TravelTo(0, 0),
                        MarkTo(1, 0),
                        SetModulation(50),
                        TravelTo(2, 1),
                        MarkTo(3, 1),
                    )
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "followed by a MarkTo"):
            RuidaJobCompiler().compile(invalid)
        RuidaJobCompiler().compile(valid)

    def test_rejects_wire_quantized_zero_length_marks(self) -> None:
        vector = replace(
            _baseline_plan().layers[0],
            events=(TravelTo(1, 1), MarkTo(1.0004, 1)),
        )
        raster = _raster_layer(
            (TravelTo(1, 1), MarkTo(1, 1))
        )

        for layer in (vector, raster):
            with self.subTest(layer=layer):
                with self.assertRaisesRegex(
                    ValueError,
                    "nonzero wire-quantized length",
                ):
                    RuidaJobCompiler().compile(JobPlan((layer,)))

    def test_metadata_bounds_round_half_up_to_hundredths(self) -> None:
        plan = JobPlan(
            layers=(
                _raster_layer(
                    (
                        TravelTo(24.125, 21.875),
                        MarkTo(20.875, 21.875),
                        TravelTo(21.125, 20.125),
                        MarkTo(20.875, 20.125),
                    )
                ),
            )
        )

        result = RuidaJobCompiler().compile(plan)

        self.assertEqual(
            result.bounds,
            Bounds(20.875, 20.125, 24.125, 21.875),
        )
        self.assertEqual(
            result.metadata_bounds,
            Bounds(20.88, 20.13, 24.13, 21.88),
        )
        self.assertEqual(result.marked_distance_mm, 3.5)

    def test_speed_must_survive_wire_quantization(self) -> None:
        layer = _baseline_plan().layers[0]
        invalid = JobPlan(
            layers=(replace(layer, speed_mm_s=0.0005),)
        )
        valid = JobPlan(
            layers=(replace(layer, speed_mm_s=0.00051),)
        )

        with self.assertRaisesRegex(ValueError, "quantize"):
            RuidaJobCompiler().compile(invalid)
        RuidaJobCompiler().compile(valid)

    def test_metadata_rounding_must_fit_absolute_field(self) -> None:
        layer = _baseline_plan().layers[0]
        safe = JobPlan(
            layers=(
                replace(
                    layer,
                    events=(
                        TravelTo(2_147_483.644, 0),
                        MarkTo(2_147_483.644, 1),
                    ),
                ),
            )
        )
        unsafe = JobPlan(
            layers=(
                replace(
                    layer,
                    events=(
                        TravelTo(2_147_483.645, 0),
                        MarkTo(2_147_483.645, 1),
                    ),
                ),
            )
        )

        result = RuidaJobCompiler().compile(safe)
        self.assertEqual(result.metadata_bounds.max_x_mm, 2_147_483.64)
        with self.assertRaisesRegex(ValueError, "Metadata-rounded"):
            RuidaJobCompiler().compile(unsafe)

    def test_marked_distance_uses_stable_summation(self) -> None:
        events: list[TravelTo | MarkTo] = [TravelTo(0, 0)]
        events.extend(MarkTo(index / 10, 0) for index in range(1, 11))
        layer = replace(
            _baseline_plan().layers[0],
            events=tuple(events),
        )

        result = RuidaJobCompiler().compile(JobPlan((layer,)))
        metric = next(
            record.values
            for record in result.program.records
            if isinstance(record, KnownCommand)
            and record.name == "set_setting"
        )

        self.assertEqual(result.marked_distance_mm, 1.0)
        self.assertEqual(metric["first_value"], 1)
        self.assertEqual(metric["second_value"], 1)

    def test_relative_motion_uses_absolute_fallback(self) -> None:
        plan = JobPlan(
            layers=(
                _raster_layer(
                    (
                        TravelTo(0, 0),
                        TravelTo(9, 0),
                        MarkTo(18, 0),
                        TravelTo(18, 1),
                        MarkTo(19, 1),
                    )
                ),
            )
        )

        result = RuidaJobCompiler().compile(plan)
        names = [
            record.name
            for record in result.program.records
            if isinstance(record, KnownCommand)
        ]
        first = names.index("move_absolute")
        self.assertEqual(
            names[first : first + 5],
            [
                "move_absolute",
                "move_absolute",
                "cut_absolute",
                "move_vertical",
                "cut_horizontal",
            ],
        )

    def test_rejects_invalid_or_incomplete_plans(self) -> None:
        cases = (
            JobPlan(()),
            JobPlan(
                (
                    LayerPlan(
                        1,
                        10,
                        10,
                        20,
                        (TravelTo(1, 1), MarkTo(2, 1)),
                    ),
                )
            ),
            JobPlan(
                (
                    LayerPlan(
                        0,
                        10,
                        10,
                        20,
                        (MarkTo(2, 1),),
                    ),
                )
            ),
            JobPlan(
                (
                    LayerPlan(
                        0,
                        10,
                        10,
                        20,
                        (TravelTo(1, 1),),
                    ),
                )
            ),
            JobPlan(
                (
                    LayerPlan(
                        0,
                        10,
                        30,
                        20,
                        (TravelTo(1, 1), MarkTo(2, 1)),
                    ),
                )
            ),
        )
        for plan in cases:
            with self.subTest(plan=plan):
                with self.assertRaises(ValueError):
                    RuidaJobCompiler().compile(plan)

    def test_rejects_unprofiled_controller_features(self) -> None:
        missing_scan_mode = JobPlan(
            (
                LayerPlan(
                    index=0,
                    speed_mm_s=100,
                    min_power_percent=10,
                    max_power_percent=20,
                    events=(TravelTo(1, 1), MarkTo(2, 1)),
                    kind="raster",
                ),
            )
        )
        vector_modulation = JobPlan(
            (
                LayerPlan(
                    index=0,
                    speed_mm_s=10,
                    min_power_percent=10,
                    max_power_percent=20,
                    events=(
                        TravelTo(1, 1),
                        SetModulation(50),
                        MarkTo(2, 1),
                    ),
                ),
            )
        )
        second_laser = JobPlan(
            (
                LayerPlan(
                    index=0,
                    speed_mm_s=10,
                    min_power_percent=10,
                    max_power_percent=20,
                    events=(TravelTo(1, 1), MarkTo(2, 1)),
                    laser_index=2,
                ),
            )
        )
        for plan in (
            missing_scan_mode,
            vector_modulation,
            second_laser,
        ):
            with self.subTest(plan=plan):
                with self.assertRaises(UnsupportedJobFeatureError):
                    RuidaJobCompiler().compile(plan)

    def test_vector_rejects_raster_scan_settings(self) -> None:
        plan = JobPlan(
            (
                LayerPlan(
                    index=0,
                    speed_mm_s=10,
                    min_power_percent=10,
                    max_power_percent=20,
                    events=(TravelTo(1, 1), MarkTo(2, 1)),
                    scan_axis="horizontal",
                    raster_strategy="unidirectional",
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "Vector layers"):
            RuidaJobCompiler().compile(plan)

    def test_plan_events_are_immutable(self) -> None:
        event = TravelTo(1, 2)
        with self.assertRaises(FrozenInstanceError):
            event.x_mm = 3

    def test_profile_labels_controlled_raster_evidence(self) -> None:
        self.assertEqual(
            LIGHTBURN_2103_644XS.envelope_evidence,
            "fixture-observed",
        )
        self.assertEqual(
            LIGHTBURN_2103_644XS.raster_semantic_evidence,
            "controlled-fixture",
        )
        self.assertEqual(
            LIGHTBURN_2103_644XS.execution_evidence,
            "operator-observed",
        )
        self.assertEqual(
            LIGHTBURN_2103_644XS.execution_evidence_source,
            "fixtures/hardware/ruida-644xs-usb-serial-v1/"
            "manifest-v1.json",
        )
        self.assertTrue(
            (
                ROOT
                / LIGHTBURN_2103_644XS.execution_evidence_source
            ).is_file()
        )
        self.assertEqual(
            tuple(
                (
                    mode.scan_axis,
                    mode.strategy,
                    mode.layer_mode,
                    mode.layer_operation,
                )
                for mode in LIGHTBURN_2103_644XS.raster_modes
            ),
            (
                ("horizontal", "unidirectional", 1, 2),
                ("horizontal", "bidirectional", 2, 1),
                ("vertical", "unidirectional", 3, 4),
                ("vertical", "bidirectional", 4, 3),
            ),
        )

    def test_custom_profile_defaults_to_no_execution_observation(self) -> None:
        profile = RuidaJobProfile(
            identifier="custom",
            producer="Example",
            producer_version="1",
            controller_profile="Example controller",
            envelope_evidence="fixture-observed",
            vector_semantic_evidence="controlled-fixture",
            raster_semantic_evidence="not-observed",
            metric_semantic_evidence="controlled-fixture",
            vector_layer_mode=0,
            vector_layer_operation=0,
            raster_modes=(),
            air_off_operation=0x12,
            air_on_operation=0x13,
            laser_enable_value=1,
            supported_laser_indices=(1,),
            element_name="",
        )

        self.assertEqual(profile.execution_evidence, "not-observed")
        self.assertIsNone(profile.execution_evidence_source)


if __name__ == "__main__":
    unittest.main()
