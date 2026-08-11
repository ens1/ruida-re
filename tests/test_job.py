"""Tests for emission-ready Ruida job compilation."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path

from ruida_re.job import (
    LIGHTBURN_2103_644XS,
    LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH,
    LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH,
    LIGHTBURN_2103_644XS_FIBER_RESEARCH,
    LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH,
    LIGHTBURN_2103_644XS_RF_RESEARCH,
    LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
    LIGHTBURN_2103_644XS_Z_RESEARCH,
    Bounds,
    Dwell,
    JobPlan,
    LaserChannelPlan,
    LayerEvent,
    LayerPlan,
    MarkTo,
    MarkWithCurrentPower,
    MarkWithPower,
    Pulse,
    RasterSection,
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
BASELINE = ROOT / "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"
MULTILAYER = ROOT / "fixtures/lightburn-2.1.03/advanced/a001-multilayer.rd"
MIXED = ROOT / "fixtures/lightburn-2.1.03/advanced/a004-mixed-vector-raster.rd"
RASTER = ROOT / "fixtures/lightburn-2.1.03/raster"
HORIZONTAL_UNIDIRECTIONAL = (
    RASTER / "r001-threshold-horizontal-unidirectional.rd"
)
GRAYSCALE_10_90 = RASTER / "r005-grayscale-range-10-90.rd"
VERTICAL_BIDIRECTIONAL = RASTER / "r008-threshold-vertical-bidirectional.rd"
DIAGONAL_GOLDENS = {
    "c002-raster-angle-045-uni": (
        508,
        "8fdca8c9a07a12c1ee7189d3b96025b609906963b38e9fa87d75f7646821175c",
    ),
    "c003-raster-angle-045-bi": (
        510,
        "2aaeb0510f5b7fb617acb0b815db7886be4f10a1d5af1d4b212776ae71d52a0d",
    ),
    "c004-raster-angle-135-uni": (
        540,
        "946bba365738e890bd23003610cb7c3b9f80a921bce514c01988318d830a1649",
    ),
    "c005-raster-angle-045-cross-hatch": (
        644,
        "131e69e02995faac0c8409da1111b02edce75c0513b149804003db841f0a4a36",
    ),
}
CAPABILITY_GOLDENS = {
    "c006-laser1-only-static": (
        492,
        "20a9fcfb4d127385f094c5ebc1aac61e05e854047f7d72a9413753048b35fd7c",
    ),
    "c007-lasers-1-and-2-static": (
        492,
        "cfb17f986d99831dbf496cf662e9b91ce855f98f508900edf020d3ff8ebf4754",
    ),
    "c008-laser2-only-static": (
        492,
        "370286dfd17251fe285bc1aa721dd2347c80e240567a5ba463904170577a6ede",
    ),
    "c009-lasers-both-laser1-power-25": (
        492,
        "e4dc35910230a5e55ba2b52b9039d33d6117a8e0bc5fc5097b5bdfbbe09311a2",
    ),
    "c010-lasers-both-laser2-power-45": (
        492,
        "83e59aadb738543a50b1536debb939171607724eb93899ac2082eece1c98c633",
    ),
    "c017-power-scale-all-050": (
        589,
        "06766fe82943ada66ec0ce994d725ed933028ae87f62bc37113ca136719149da",
    ),
    "c019-power-scale-sequence-000-050-100": (
        539,
        "1e2ddcad81cace64e697954b0494a8cdd12e73176d4497e42b623a29e3340a88",
    ),
    "c021-power-scale-sequence-repeated-050": (
        564,
        "f46f443b8e862757e754dec2c3121825ab1555fdabdbca14ddb8f1f237ebdebf",
    ),
    "c022-delay-zero-baseline": (
        492,
        "20a9fcfb4d127385f094c5ebc1aac61e05e854047f7d72a9413753048b35fd7c",
    ),
    "c023-start-delay-100": (
        499,
        "ae494b42b176bfc8b81379a47d1ccc47827406885e6359a108361dd815fef6a9",
    ),
    "c024-start-delay-200": (
        499,
        "e888d4d884b9bb2249a8042ea1a7e2087df06aaaaaf3a406356e085f25040fb0",
    ),
    "c025-end-delay-100": (
        499,
        "feea47a3581b70bb5d64d182261f216775bf1212d1d60b61d1243940d23f5512",
    ),
    "c026-end-delay-200": (
        499,
        "64d655b3a288a304f67f65617fe7c4671d68e738a45b923ea154e6421fc96fdb",
    ),
    "c033-dot-mode-disabled": (
        492,
        "20a9fcfb4d127385f094c5ebc1aac61e05e854047f7d72a9413753048b35fd7c",
    ),
    "c034-dot-mode-enabled": (
        578,
        "916f6ef733601851a10e49e47ce2481bc503878c409760ac429db0e526d61e4f",
    ),
    "c035-dot-time-200": (
        578,
        "c5c84802cd1eb7b702b3ea8905d3bc8b7fa8d54af50ae9a131e7f202857e2cf2",
    ),
    "c036-dot-spacing-2": (
        528,
        "c9a2bbeede1378b13dc0f936f81e3c90bfdab0dd32e1866b984226e4ae9716fe",
    ),
    "c037-frequency-override-disabled-20000": (
        492,
        "20a9fcfb4d127385f094c5ebc1aac61e05e854047f7d72a9413753048b35fd7c",
    ),
    "c038-frequency-override-enabled-20000": (
        510,
        "f98d2d1364065bbe60bb6a02f963d629ffc695e552cd009d1a025f4b33959a07",
    ),
    "c039-frequency-override-enabled-10000": (
        510,
        "5a9cabd5cbd55ed55cc470ab05de83a8094a7d3e38e97ad963f56f25565054cc",
    ),
    "c040-fiber-pulse-width-zero": (
        501,
        "0f245c98725c62e5c00698cc826e3ab8faae4be84a66f5e8e712d26697339303",
    ),
    "c041-fiber-pulse-width-100": (
        501,
        "402a60f032796417a9cd053b9c2b523c5c0b16d6b0d7e3af21c444a127a3e4cb",
    ),
    "c042-fiber-pulse-width-200": (
        501,
        "b55321af766f95a18bbb5714b32987ce20da2fb4687c3a718fc83ef108429078",
    ),
    "c043-z-offset-positive-1": (
        595,
        "51e1c1e908c64850f0949c7c5912df6250b4b359c40bf7fcdb9e85308cc89c5c",
    ),
    "c044-z-offset-negative-1": (
        595,
        "62dbc24d8ce686c44c9063b09f616cb83957a701685dd992739dc7fed4641cef",
    ),
}


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


def _capability_channels(
    *,
    laser_1_power: float = 20,
    laser_1_enabled: bool = True,
    laser_2_power: float = 40,
    laser_2_enabled: bool = False,
    laser_1_max_power: float | None = None,
) -> tuple[LaserChannelPlan, ...]:
    return (
        LaserChannelPlan(
            1,
            laser_1_enabled,
            laser_1_power,
            laser_1_power if laser_1_max_power is None else laser_1_max_power,
        ),
        LaserChannelPlan(
            2,
            laser_2_enabled,
            laser_2_power,
            laser_2_power,
        ),
    )


def _capability_vector_layer(
    events: tuple[LayerEvent, ...],
) -> LayerPlan:
    return LayerPlan(
        index=0,
        speed_mm_s=10,
        min_power_percent=20,
        max_power_percent=20,
        events=events,
        laser_channels=_capability_channels(),
    )


def _z_offset_events() -> tuple[TravelTo | MarkTo, ...]:
    return (
        TravelTo(20.25, 20),
        MarkTo(23.75, 20),
        TravelTo(20.25, 20.5),
        MarkTo(23.75, 20.5),
        TravelTo(20.75, 20),
        MarkTo(21.25, 20),
        TravelTo(22.25, 20),
        MarkTo(23.75, 20),
        TravelTo(20.25, 20.5),
        MarkTo(23.75, 20.5),
        TravelTo(23.25, 20),
        MarkTo(23.75, 20),
        TravelTo(20.25, 20.5),
        MarkTo(21.25, 20.5),
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
    events: list[TravelTo | MarkTo | SetModulation] = [TravelTo(23.5, 20.25)]
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


def _diagonal_45_unidirectional_events() -> tuple[TravelTo | MarkTo, ...]:
    return (
        TravelTo(21.646, 22.414),
        MarkTo(21.293, 22.061),
        TravelTo(22.0, 22.061),
        MarkTo(21.293, 21.354),
        TravelTo(20.939, 21.0),
        MarkTo(20.586, 20.646),
        TravelTo(23.061, 22.414),
        MarkTo(21.293, 20.646),
    )


def _diagonal_45_bidirectional_events() -> tuple[TravelTo | MarkTo, ...]:
    return (
        TravelTo(21.646, 22.414),
        MarkTo(21.293, 22.061),
        TravelTo(20.586, 20.646),
        MarkTo(20.939, 21.0),
        TravelTo(21.293, 21.354),
        MarkTo(22.0, 22.061),
        TravelTo(23.061, 22.414),
        MarkTo(21.293, 20.646),
    )


def _diagonal_135_unidirectional_events() -> tuple[TravelTo | MarkTo, ...]:
    return (
        TravelTo(20.939, 21.0),
        MarkTo(21.646, 20.293),
        TravelTo(20.939, 21.707),
        MarkTo(21.293, 21.354),
        TravelTo(21.646, 21.0),
        MarkTo(22.354, 20.293),
        TravelTo(22.0, 21.354),
        MarkTo(22.707, 20.646),
        TravelTo(22.0, 22.061),
        MarkTo(23.061, 21.0),
        TravelTo(23.414, 20.646),
        MarkTo(23.768, 20.293),
        TravelTo(22.707, 22.061),
        MarkTo(23.414, 21.354),
    )


def _planned_path_raster_plan(
    *sections: tuple[TravelTo | MarkTo, ...],
    reported_job_metric_mm: float | None = None,
    declared_metadata_bounds: Bounds | None = None,
) -> JobPlan:
    return JobPlan(
        layers=(
            LayerPlan(
                index=0,
                speed_mm_s=100,
                min_power_percent=50,
                max_power_percent=50,
                events=(),
                kind="raster",
                raster_processing="planned-path",
                raster_sections=tuple(
                    RasterSection(events) for events in sections
                ),
                declared_metadata_bounds=declared_metadata_bounds,
            ),
        ),
        reported_job_metric_mm=reported_job_metric_mm,
        declared_metadata_bounds=declared_metadata_bounds,
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
    def _assert_capability_golden(
        self,
        identifier: str,
        profile: RuidaJobProfile,
        plan: JobPlan,
    ) -> None:
        encoded = RuidaJobCompiler(profile).compile(plan).encode_rd()
        expected_size, expected_sha256 = CAPABILITY_GOLDENS[identifier]
        self.assertEqual(len(encoded), expected_size)
        self.assertEqual(sha256(encoded).hexdigest(), expected_sha256)

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

    def test_dual_laser_channels_match_capability_goldens(self) -> None:
        cases = (
            ("c006-laser1-only-static", 20, True, 40, False),
            ("c007-lasers-1-and-2-static", 20, True, 40, True),
            ("c008-laser2-only-static", 20, False, 40, True),
            ("c009-lasers-both-laser1-power-25", 25, True, 40, True),
            ("c010-lasers-both-laser2-power-45", 20, True, 45, True),
        )
        events = (TravelTo(20, 20), MarkTo(30, 20))

        for identifier, power_1, enabled_1, power_2, enabled_2 in cases:
            with self.subTest(case=identifier):
                layer = replace(
                    _capability_vector_layer(events),
                    min_power_percent=power_1,
                    max_power_percent=power_1,
                    laser_channels=_capability_channels(
                        laser_1_power=power_1,
                        laser_1_enabled=enabled_1,
                        laser_2_power=power_2,
                        laser_2_enabled=enabled_2,
                    ),
                )
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH,
                    JobPlan((layer,)),
                )

    def test_dwell_events_match_capability_goldens(self) -> None:
        cases = (
            (
                "c022-delay-zero-baseline",
                (TravelTo(20, 20), MarkTo(30, 20)),
            ),
            (
                "c023-start-delay-100",
                (TravelTo(20, 20), Dwell(100), MarkTo(30, 20)),
            ),
            (
                "c024-start-delay-200",
                (TravelTo(20, 20), Dwell(200), MarkTo(30, 20)),
            ),
            (
                "c025-end-delay-100",
                (TravelTo(20, 20), MarkTo(30, 20), Dwell(100)),
            ),
            (
                "c026-end-delay-200",
                (TravelTo(20, 20), MarkTo(30, 20), Dwell(200)),
            ),
        )
        for identifier, events in cases:
            with self.subTest(case=identifier):
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
                    JobPlan((_capability_vector_layer(events),)),
                )

    def test_stationary_pulses_match_capability_goldens(self) -> None:
        self._assert_capability_golden(
            "c033-dot-mode-disabled",
            LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
            JobPlan(
                (
                    _capability_vector_layer(
                        (TravelTo(20, 20), MarkTo(30, 20))
                    ),
                )
            ),
        )
        cases = (
            ("c034-dot-mode-enabled", 100, 1, 10),
            ("c035-dot-time-200", 200, 1, 10),
            ("c036-dot-spacing-2", 100, 2, 5),
        )
        bounds = Bounds(20, 20, 30, 20)
        for identifier, duration, spacing, count in cases:
            with self.subTest(case=identifier):
                events: list[LayerEvent] = []
                for index in range(count):
                    events.extend(
                        (
                            TravelTo(20 + index * spacing, 20),
                            Pulse(duration),
                        )
                    )
                layer = replace(
                    _capability_vector_layer(tuple(events)),
                    declared_metadata_bounds=bounds,
                )
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
                    JobPlan((layer,), declared_metadata_bounds=bounds),
                )

    def test_layer_frequency_matches_capability_goldens(self) -> None:
        for identifier, frequency_hz in (
            ("c037-frequency-override-disabled-20000", None),
            ("c038-frequency-override-enabled-20000", 20_000),
            ("c039-frequency-override-enabled-10000", 10_000),
        ):
            with self.subTest(case=identifier):
                layer = replace(
                    _capability_vector_layer(
                        (TravelTo(20, 20), MarkTo(30, 20))
                    ),
                    frequency_hz=frequency_hz,
                )
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_RF_RESEARCH,
                    JobPlan((layer,)),
                )

    def test_fiber_pulse_width_matches_capability_goldens(self) -> None:
        for identifier, pulse_width_ns in (
            ("c040-fiber-pulse-width-zero", 0),
            ("c041-fiber-pulse-width-100", 100),
            ("c042-fiber-pulse-width-200", 200),
        ):
            with self.subTest(case=identifier):
                layer = replace(
                    _capability_vector_layer(
                        (TravelTo(20, 20), MarkTo(30, 20))
                    ),
                    pulse_width_ns=pulse_width_ns,
                )
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_FIBER_RESEARCH,
                    JobPlan((layer,)),
                )

        omitted = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_FIBER_RESEARCH
        ).compile(
            JobPlan(
                (
                    _capability_vector_layer(
                        (TravelTo(20, 20), MarkTo(30, 20))
                    ),
                )
            )
        )
        names = [
            record.name
            for record in omitted.program.records
            if isinstance(record, KnownCommand)
        ]
        self.assertNotIn("layer_fiber_pulse_width", names)

    def test_paired_z_offsets_match_capability_goldens(self) -> None:
        for identifier, z_offset_mm in (
            ("c043-z-offset-positive-1", 1),
            ("c044-z-offset-negative-1", -1),
        ):
            with self.subTest(case=identifier):
                layer = LayerPlan(
                    index=0,
                    speed_mm_s=100,
                    min_power_percent=50,
                    max_power_percent=50,
                    events=_z_offset_events(),
                    kind="raster",
                    scan_axis="horizontal",
                    raster_strategy="unidirectional",
                    z_offset_mm=z_offset_mm,
                )
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_Z_RESEARCH,
                    JobPlan((layer,)),
                )

    def test_dynamic_vector_power_matches_capability_goldens(self) -> None:
        layer_channels = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=70,
        )
        effective = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=40,
        )
        cases = (
            (
                "c017-power-scale-all-050",
                (
                    TravelTo(20, 20),
                    MarkWithPower(30, 20, effective),
                    MarkWithPower(40, 20, effective),
                    MarkWithPower(50, 20, effective),
                ),
            ),
            (
                "c019-power-scale-sequence-000-050-100",
                (
                    TravelTo(20, 20),
                    MarkTo(30, 20),
                    MarkWithPower(40, 20, effective),
                    MarkWithCurrentPower(50, 20),
                ),
            ),
            (
                "c021-power-scale-sequence-repeated-050",
                (
                    TravelTo(20, 20),
                    MarkWithPower(30, 20, effective),
                    MarkWithPower(40, 20, effective),
                    MarkWithCurrentPower(50, 20),
                ),
            ),
        )
        for identifier, events in cases:
            with self.subTest(case=identifier):
                layer = LayerPlan(
                    index=0,
                    speed_mm_s=10,
                    min_power_percent=10,
                    max_power_percent=70,
                    events=events,
                    laser_channels=layer_channels,
                )
                self._assert_capability_golden(
                    identifier,
                    LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH,
                    JobPlan((layer,)),
                )

    def test_dynamic_power_restores_baseline_before_normal_mark(self) -> None:
        layer_channels = _capability_channels(
            laser_1_power=5,
            laser_1_max_power=15,
        )
        reduced = _capability_channels(
            laser_1_power=5,
            laser_1_max_power=5,
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=100,
            min_power_percent=5,
            max_power_percent=15,
            events=(
                TravelTo(30, 75),
                MarkTo(60, 75),
                MarkWithPower(90, 75, reduced),
                MarkTo(120, 75),
            ),
            laser_channels=layer_channels,
        )

        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        ).compile(JobPlan((layer,)))
        records = tuple(
            record
            for record in result.program.records
            if isinstance(record, KnownCommand)
        )
        cut_indices = tuple(
            index
            for index, record in enumerate(records)
            if record.name == "cut_absolute"
        )

        self.assertEqual(len(cut_indices), 3)
        reduced_envelope = records[cut_indices[1] - 7 : cut_indices[1]]
        restore_envelope = records[cut_indices[2] - 7 : cut_indices[2]]
        expected_names = (
            "layer_control",
            "select_layer",
            "laser_1_min_power",
            "laser_1_max_power",
            "laser_2_min_power",
            "laser_2_max_power",
            "external_io",
        )
        self.assertEqual(
            tuple(record.name for record in reduced_envelope),
            expected_names,
        )
        self.assertEqual(
            tuple(record.name for record in restore_envelope),
            expected_names,
        )
        self.assertEqual(
            reduced_envelope[3].values["power_percent"],
            5,
        )
        self.assertEqual(
            restore_envelope[3].values["power_percent"],
            15,
        )
        self.assertEqual(
            restore_envelope[0].values,
            {"operation": 5},
        )
        self.assertEqual(restore_envelope[1].values, {"layer": 0})
        self.assertEqual(restore_envelope[6].values, {"value": 0})

    def test_consecutive_dynamic_marks_set_power_then_restore(self) -> None:
        layer_channels = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=70,
        )
        reduced = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=40,
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=10,
            max_power_percent=70,
            events=(
                TravelTo(20, 20),
                MarkWithPower(30, 20, reduced),
                MarkWithPower(40, 20, reduced),
                MarkTo(50, 20),
            ),
            laser_channels=layer_channels,
        )

        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        ).compile(JobPlan((layer,)))
        records = tuple(
            record
            for record in result.program.records
            if isinstance(record, KnownCommand)
        )
        cut_indices = tuple(
            index
            for index, record in enumerate(records)
            if record.name == "cut_absolute"
        )

        self.assertEqual(len(cut_indices), 3)
        self.assertEqual(
            tuple(
                records[index - 4].values["power_percent"]
                for index in cut_indices
            ),
            (40, 40, 70),
        )

    def test_current_power_mark_requires_an_active_override(self) -> None:
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        channels = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=70,
        )
        reduced = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=40,
        )
        cases = (
            (
                TravelTo(20, 20),
                MarkWithCurrentPower(30, 20),
            ),
            (
                TravelTo(20, 20),
                MarkWithPower(30, 20, reduced),
                MarkTo(40, 20),
                MarkWithCurrentPower(50, 20),
            ),
        )
        for events in cases:
            layer = LayerPlan(
                index=0,
                speed_mm_s=10,
                min_power_percent=10,
                max_power_percent=70,
                events=events,
                laser_channels=channels,
            )
            with (
                self.subTest(events=events),
                self.assertRaisesRegex(ValueError, "preceding MarkWithPower"),
            ):
                compiler.compile(JobPlan((layer,)))

    def test_dynamic_power_state_starts_at_layer_power(self) -> None:
        channels = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=70,
        )
        reduced = _capability_channels(
            laser_1_power=10,
            laser_1_max_power=40,
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=10,
            max_power_percent=70,
            events=(
                TravelTo(20, 20),
                MarkTo(30, 20),
                MarkWithPower(40, 20, reduced),
            ),
            laser_channels=channels,
        )

        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        ).compile(JobPlan((layer,)))
        records = tuple(
            record
            for record in result.program.records
            if isinstance(record, KnownCommand)
        )
        dynamic_controls = tuple(
            record
            for record in records
            if record.name == "layer_control"
            and record.values == {"operation": 5}
        )
        final_cut_index = max(
            index
            for index, record in enumerate(records)
            if record.name == "cut_absolute"
        )
        active_power_names = {
            "laser_1_min_power",
            "laser_1_max_power",
            "laser_2_min_power",
            "laser_2_max_power",
        }

        self.assertEqual(len(dynamic_controls), 1)
        self.assertFalse(
            any(
                record.name in active_power_names
                for record in records[final_cut_index + 1 :]
            )
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
        result = RuidaJobCompiler().compile(_horizontal_unidirectional_plan())

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
        result = RuidaJobCompiler().compile(_vertical_bidirectional_plan())

        self.assertEqual(
            result.encode_rd(),
            VERTICAL_BIDIRECTIONAL.read_bytes(),
        )
        self.assertEqual(result.marked_distance_mm, 5.5)

    def test_planned_path_raster_matches_diagonal_goldens(self) -> None:
        diagonal_45_bounds = Bounds(20.59, 20.65, 23.06, 22.42)
        plans = {
            "c002-raster-angle-045-uni": _planned_path_raster_plan(
                _diagonal_45_unidirectional_events(),
                reported_job_metric_mm=6,
                declared_metadata_bounds=diagonal_45_bounds,
            ),
            "c003-raster-angle-045-bi": _planned_path_raster_plan(
                _diagonal_45_bidirectional_events(),
                reported_job_metric_mm=6,
                declared_metadata_bounds=diagonal_45_bounds,
            ),
            "c004-raster-angle-135-uni": _planned_path_raster_plan(
                _diagonal_135_unidirectional_events(),
                reported_job_metric_mm=8,
            ),
            "c005-raster-angle-045-cross-hatch": (
                _planned_path_raster_plan(
                    _diagonal_45_unidirectional_events(),
                    _diagonal_135_unidirectional_events(),
                    reported_job_metric_mm=14,
                )
            ),
        }

        for identifier, plan in plans.items():
            with self.subTest(case=identifier):
                encoded = RuidaJobCompiler(
                    LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
                ).compile(plan).encode_rd()
                expected_size, expected_sha256 = DIAGONAL_GOLDENS[identifier]
                self.assertEqual(len(encoded), expected_size)
                self.assertEqual(
                    sha256(encoded).hexdigest(),
                    expected_sha256,
                )

    def test_cross_hatch_uses_two_complete_raster_sections(self) -> None:
        plan = _planned_path_raster_plan(
            _diagonal_45_unidirectional_events(),
            _diagonal_135_unidirectional_events(),
            reported_job_metric_mm=14,
        )

        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
        ).compile(plan)
        commands = [
            record
            for record in result.program.records
            if isinstance(record, KnownCommand)
        ]
        first_end = next(
            index
            for index, record in enumerate(commands)
            if record.name == "block_end"
        )
        boundary = commands[first_end : first_end + 4]

        self.assertEqual(
            [record.name for record in boundary],
            [
                "block_end",
                "layer_control",
                "layer_control",
                "select_layer",
            ],
        )
        self.assertEqual(boundary[1].values, {"operation": 5})
        self.assertEqual(boundary[2].values, {"operation": 0})
        self.assertEqual(
            sum(record.name == "block_end" for record in commands),
            3,
        )

    def test_planned_path_raster_metric_has_deterministic_default(
        self,
    ) -> None:
        plan = _planned_path_raster_plan(_diagonal_45_unidirectional_events())

        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
        ).compile(plan)
        metric = next(
            record
            for record in result.program.records
            if isinstance(record, KnownCommand)
            and record.name == "set_setting"
        )

        self.assertAlmostEqual(result.marked_distance_mm, 4.49932, 5)
        self.assertEqual(metric.values["first_value"], 4)
        self.assertEqual(metric.values["second_value"], 4)

    def test_declared_metadata_bounds_are_optional_plan_data(self) -> None:
        events = _diagonal_45_unidirectional_events()
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
        )
        derived = compiler.compile(_planned_path_raster_plan(events))
        declared_bounds = Bounds(20.59, 20.65, 23.06, 22.42)
        declared = compiler.compile(
            _planned_path_raster_plan(
                events,
                declared_metadata_bounds=declared_bounds,
            )
        )

        self.assertEqual(derived.bounds, declared.bounds)
        self.assertEqual(derived.metadata_bounds.max_y_mm, 22.41)
        self.assertEqual(declared.metadata_bounds, declared_bounds)
        self.assertEqual(declared.metadata_layer_bounds, (declared_bounds,))

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
        unidirectional = JobPlan(layers=(_raster_layer(events),))
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
        raster = _raster_layer((TravelTo(1, 1), MarkTo(1, 1)))

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
        invalid = JobPlan(layers=(replace(layer, speed_mm_s=0.0005),))
        valid = JobPlan(layers=(replace(layer, speed_mm_s=0.00051),))

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

    def test_advanced_controls_require_research_profiles(self) -> None:
        channels = _capability_channels()
        effective = _capability_channels(laser_1_power=15)
        vector = _capability_vector_layer((TravelTo(20, 20), MarkTo(30, 20)))
        raster = _raster_layer((TravelTo(20, 20), MarkTo(30, 20)))
        plans = (
            JobPlan((vector,)),
            JobPlan(
                (
                    replace(
                        vector,
                        events=(
                            TravelTo(20, 20),
                            Dwell(100),
                            MarkTo(30, 20),
                        ),
                    ),
                )
            ),
            JobPlan(
                (
                    replace(
                        vector,
                        events=(TravelTo(20, 20), Pulse(100)),
                    ),
                )
            ),
            JobPlan((replace(vector, frequency_hz=20_000),)),
            JobPlan((replace(vector, pulse_width_ns=0),)),
            JobPlan((replace(raster, z_offset_mm=1),)),
            JobPlan(
                (
                    replace(
                        vector,
                        events=(
                            TravelTo(20, 20),
                            MarkWithPower(30, 20, effective),
                        ),
                        laser_channels=channels,
                    ),
                )
            ),
        )
        for plan in plans:
            with (
                self.subTest(plan=plan),
                self.assertRaises(UnsupportedJobFeatureError),
            ):
                RuidaJobCompiler().compile(plan)

    def test_legacy_mark_rejects_power_fields_below_wire_floor(self) -> None:
        layer = _baseline_plan().layers[0]
        for minimum, maximum in (
            (0, 0),
            (0.001, 0.001),
            (0.09, 0.09),
            (0, 20),
        ):
            plan = JobPlan(
                (
                    replace(
                        layer,
                        min_power_percent=minimum,
                        max_power_percent=maximum,
                    ),
                )
            )
            with (
                self.subTest(minimum=minimum, maximum=maximum),
                self.assertRaisesRegex(
                    ValueError,
                    "at or above raw power 16",
                ),
            ):
                RuidaJobCompiler().compile(plan)

        floor = replace(
            layer,
            min_power_percent=0.1,
            max_power_percent=0.1,
        )
        result = RuidaJobCompiler().compile(JobPlan((floor,)))
        self.assertTrue(result.encode_rd())

    def test_explicit_mark_rejects_enabled_power_below_floor(self) -> None:
        for minimum, maximum in ((0, 0), (0, 20)):
            channels = (
                LaserChannelPlan(1, True, minimum, maximum),
                LaserChannelPlan(2, False, 40, 40),
            )
            layer = LayerPlan(
                index=0,
                speed_mm_s=10,
                min_power_percent=minimum,
                max_power_percent=maximum,
                events=(TravelTo(20, 20), MarkTo(30, 20)),
                laser_channels=channels,
            )
            with (
                self.subTest(minimum=minimum, maximum=maximum),
                self.assertRaisesRegex(
                    ValueError,
                    "at or above raw power 16",
                ),
            ):
                RuidaJobCompiler(
                    LIGHTBURN_2103_644XS_STATIONARY_RESEARCH
                ).compile(JobPlan((layer,)))

    def test_zero_inactive_channel_does_not_block_positive_mark(self) -> None:
        channels = (
            LaserChannelPlan(1, True, 20, 20),
            LaserChannelPlan(2, False, 0, 0),
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=20,
            max_power_percent=20,
            events=(TravelTo(20, 20), MarkTo(30, 20)),
            laser_channels=channels,
        )

        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH
        ).compile(JobPlan((layer,)))

        self.assertTrue(result.encode_rd())

    def test_each_enabled_channel_requires_observed_power_floor(self) -> None:
        channels = (
            LaserChannelPlan(1, True, 20, 20),
            LaserChannelPlan(2, True, 0, 0),
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=20,
            max_power_percent=20,
            events=(TravelTo(20, 20), MarkTo(30, 20)),
            laser_channels=channels,
        )
        with self.assertRaisesRegex(
            ValueError,
            "every enabled laser channel minimum and maximum",
        ):
            RuidaJobCompiler(
                LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH
            ).compile(JobPlan((layer,)))

    def test_dynamic_mark_rejects_zero_enabled_override(self) -> None:
        layer_channels = (
            LaserChannelPlan(1, True, 0, 70),
            LaserChannelPlan(2, False, 40, 40),
        )
        zero_override = (
            LaserChannelPlan(1, True, 0, 0),
            LaserChannelPlan(2, False, 40, 40),
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=0,
            max_power_percent=70,
            events=(
                TravelTo(20, 20),
                MarkWithPower(30, 20, zero_override),
            ),
            laser_channels=layer_channels,
        )
        with self.assertRaisesRegex(
            ValueError,
            "at or above raw power 16",
        ):
            RuidaJobCompiler(
                LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
            ).compile(JobPlan((layer,)))

    def test_dynamic_override_does_not_mask_unsafe_layer_setup(self) -> None:
        layer_channels = (
            LaserChannelPlan(1, True, 0, 70),
            LaserChannelPlan(2, False, 40, 40),
        )
        positive_override = (
            LaserChannelPlan(1, True, 10, 40),
            LaserChannelPlan(2, False, 40, 40),
        )
        layer = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=0,
            max_power_percent=70,
            events=(
                TravelTo(20, 20),
                MarkWithPower(30, 20, positive_override),
            ),
            laser_channels=layer_channels,
        )
        with self.assertRaisesRegex(
            ValueError,
            "at or above raw power 16",
        ):
            RuidaJobCompiler(
                LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
            ).compile(JobPlan((layer,)))

    def test_raster_mark_rejects_modulation_below_observed_floor(self) -> None:
        for modulation in (0, 0.09):
            layer = _raster_layer(
                (
                    TravelTo(20, 20),
                    SetModulation(modulation),
                    MarkTo(30, 20),
                )
            )
            with (
                self.subTest(modulation=modulation),
                self.assertRaisesRegex(
                    ValueError,
                    "modulation must encode at or above raw power 16",
                ),
            ):
                RuidaJobCompiler().compile(JobPlan((layer,)))

    def test_zero_power_travel_does_not_become_marking(self) -> None:
        layer = replace(
            _baseline_plan().layers[0],
            min_power_percent=0,
            max_power_percent=0,
            events=(TravelTo(20, 20), TravelTo(30, 20)),
        )
        with self.assertRaisesRegex(
            ValueError,
            "at least one marking event",
        ):
            RuidaJobCompiler().compile(JobPlan((layer,)))

    def test_explicit_laser_channels_fail_closed(self) -> None:
        layer = _capability_vector_layer((TravelTo(20, 20), MarkTo(30, 20)))
        invalid_channels = (
            (LaserChannelPlan(1, True, 20, 20),),
            (
                LaserChannelPlan(2, False, 40, 40),
                LaserChannelPlan(1, True, 20, 20),
            ),
            (
                LaserChannelPlan(1, True, 20, 20),
                LaserChannelPlan(1, False, 40, 40),
            ),
            (
                LaserChannelPlan(1, False, 20, 20),
                LaserChannelPlan(2, False, 40, 40),
            ),
            (
                LaserChannelPlan(1, True, 25, 25),
                LaserChannelPlan(2, False, 40, 40),
            ),
        )
        for channels in invalid_channels:
            with (
                self.subTest(channels=channels),
                self.assertRaises((ValueError, UnsupportedJobFeatureError)),
            ):
                RuidaJobCompiler(
                    LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH
                ).compile(JobPlan((replace(layer, laser_channels=channels),)))

        for laser_index in (True, 2):
            with (
                self.subTest(laser_index=laser_index),
                self.assertRaises(ValueError),
            ):
                RuidaJobCompiler(
                    LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH
                ).compile(JobPlan((replace(layer, laser_index=laser_index),)))

    def test_research_profiles_reject_unobserved_layer_scope(self) -> None:
        channels = _capability_channels()
        vector = _capability_vector_layer(
            (TravelTo(20, 20), MarkTo(30, 20))
        )
        raster = replace(
            _raster_layer(
                (TravelTo(20, 20), MarkTo(30, 20)),
                min_power_percent=20,
                max_power_percent=20,
            ),
            laser_channels=channels,
        )
        planned = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=20,
            max_power_percent=20,
            events=(),
            kind="raster",
            raster_processing="planned-path",
            raster_sections=(
                RasterSection((TravelTo(20, 20), MarkTo(30, 20))),
            ),
            laser_channels=channels,
        )
        modulated = replace(
            raster,
            events=(
                TravelTo(20, 20),
                SetModulation(50),
                MarkTo(30, 20),
            ),
        )
        second = replace(
            vector,
            index=1,
            events=(TravelTo(20, 21), MarkTo(30, 21)),
        )
        profiles = (
            LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH,
            LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
            LIGHTBURN_2103_644XS_RF_RESEARCH,
            LIGHTBURN_2103_644XS_FIBER_RESEARCH,
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH,
        )
        for profile in profiles:
            compiler = RuidaJobCompiler(profile)
            with (
                self.subTest(profile=profile.identifier, case="raster"),
                self.assertRaisesRegex(
                    UnsupportedJobFeatureError,
                    "outside this job profile's scope",
                ),
            ):
                compiler.compile(JobPlan((raster,)))
            with (
                self.subTest(profile=profile.identifier, case="multilayer"),
                self.assertRaisesRegex(
                    UnsupportedJobFeatureError,
                    "requires exactly 1",
                ),
            ):
                compiler.compile(JobPlan((vector, second)))

        dual = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH
        )
        for layer in (planned, modulated):
            with (
                self.subTest(layer=layer),
                self.assertRaisesRegex(
                    UnsupportedJobFeatureError,
                    "outside this job profile's scope",
                ),
            ):
                dual.compile(JobPlan((layer,)))

    def test_single_head_research_profiles_reject_head2(self) -> None:
        channels = (
            LaserChannelPlan(1, False, 20, 20),
            LaserChannelPlan(2, True, 40, 40),
        )
        base = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=20,
            max_power_percent=20,
            events=(TravelTo(20, 20), MarkTo(30, 20)),
            laser_channels=channels,
        )
        cases = (
            (
                LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
                replace(
                    base,
                    events=(
                        TravelTo(20, 20),
                        Dwell(100),
                        MarkTo(30, 20),
                    ),
                ),
            ),
            (
                LIGHTBURN_2103_644XS_RF_RESEARCH,
                replace(base, frequency_hz=20_000),
            ),
            (
                LIGHTBURN_2103_644XS_FIBER_RESEARCH,
                replace(base, pulse_width_ns=100),
            ),
            (
                LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH,
                replace(
                    base,
                    events=(
                        TravelTo(20, 20),
                        MarkWithPower(30, 20, channels),
                    ),
                ),
            ),
        )
        for profile, layer in cases:
            with (
                self.subTest(profile=profile.identifier),
                self.assertRaisesRegex(
                    UnsupportedJobFeatureError,
                    "Laser enable mask 2",
                ),
            ):
                RuidaJobCompiler(profile).compile(JobPlan((layer,)))

    def test_research_control_limits_are_declarative(self) -> None:
        vector = _capability_vector_layer(
            (TravelTo(20, 20), MarkTo(30, 20))
        )
        raster = _raster_layer((TravelTo(20, 20), MarkTo(30, 20)))
        cases = (
            (
                LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
                replace(
                    vector,
                    events=(
                        TravelTo(20, 20),
                        Dwell(200.001),
                        MarkTo(30, 20),
                    ),
                ),
            ),
            (
                LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
                replace(
                    vector,
                    events=(TravelTo(20, 20), Pulse(200.001)),
                ),
            ),
            (
                LIGHTBURN_2103_644XS_RF_RESEARCH,
                replace(vector, frequency_hz=9_999),
            ),
            (
                LIGHTBURN_2103_644XS_RF_RESEARCH,
                replace(vector, frequency_hz=20_001),
            ),
            (
                LIGHTBURN_2103_644XS_FIBER_RESEARCH,
                replace(vector, pulse_width_ns=201),
            ),
            (
                LIGHTBURN_2103_644XS_Z_RESEARCH,
                replace(raster, z_offset_mm=1.001),
            ),
            (
                LIGHTBURN_2103_644XS_Z_RESEARCH,
                replace(raster, z_offset_mm=-1.001),
            ),
        )
        for profile, layer in cases:
            with (
                self.subTest(profile=profile.identifier, layer=layer),
                self.assertRaises(UnsupportedJobFeatureError),
            ):
                RuidaJobCompiler(profile).compile(JobPlan((layer,)))

        stationary_mode = (
            LIGHTBURN_2103_644XS_STATIONARY_RESEARCH.stationary_event_mode
        )
        assert stationary_mode is not None
        stationary_profile = replace(
            LIGHTBURN_2103_644XS_STATIONARY_RESEARCH,
            stationary_event_mode=replace(
                stationary_mode,
                max_duration_ms=250,
            ),
        )
        RuidaJobCompiler(stationary_profile).compile(
            JobPlan(
                (
                    replace(
                        vector,
                        events=(
                            TravelTo(20, 20),
                            Dwell(250),
                            MarkTo(30, 20),
                        ),
                    ),
                )
            )
        )

        frequency_mode = (
            LIGHTBURN_2103_644XS_RF_RESEARCH.layer_frequency_mode
        )
        assert frequency_mode is not None
        frequency_profile = replace(
            LIGHTBURN_2103_644XS_RF_RESEARCH,
            layer_frequency_mode=replace(
                frequency_mode,
                minimum_hz=9_999,
            ),
        )
        RuidaJobCompiler(frequency_profile).compile(
            JobPlan((replace(vector, frequency_hz=9_999),))
        )

        pulse_width_mode = (
            LIGHTBURN_2103_644XS_FIBER_RESEARCH.fiber_pulse_width_mode
        )
        assert pulse_width_mode is not None
        pulse_width_profile = replace(
            LIGHTBURN_2103_644XS_FIBER_RESEARCH,
            fiber_pulse_width_mode=replace(
                pulse_width_mode,
                maximum_ns=201,
            ),
        )
        RuidaJobCompiler(pulse_width_profile).compile(
            JobPlan((replace(vector, pulse_width_ns=201),))
        )

        z_mode = LIGHTBURN_2103_644XS_Z_RESEARCH.paired_z_offset_mode
        assert z_mode is not None
        z_profile = replace(
            LIGHTBURN_2103_644XS_Z_RESEARCH,
            paired_z_offset_mode=replace(
                z_mode,
                maximum_abs_offset_mm=1.001,
            ),
        )
        RuidaJobCompiler(z_profile).compile(
            JobPlan((replace(raster, z_offset_mm=1.001),))
        )

    def test_invalid_research_profile_policies_fail_closed(self) -> None:
        dual_mode = (
            LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH.laser_channel_mode
        )
        frequency_mode = (
            LIGHTBURN_2103_644XS_RF_RESEARCH.layer_frequency_mode
        )
        dynamic_mode = (
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
            .dynamic_vector_power_mode
        )
        assert dual_mode is not None
        assert frequency_mode is not None
        assert dynamic_mode is not None
        profiles = (
            replace(
                LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH,
                required_layer_count=2,
            ),
            replace(
                LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH,
                laser_channel_mode=replace(
                    dual_mode,
                    allowed_enable_masks=(4,),
                ),
            ),
            replace(
                LIGHTBURN_2103_644XS_RF_RESEARCH,
                layer_frequency_mode=replace(
                    frequency_mode,
                    minimum_hz=20_001,
                    maximum_hz=20_000,
                ),
            ),
            replace(
                LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH,
                dynamic_vector_power_mode=replace(
                    dynamic_mode,
                    mutable_max_power_indices=(),
                ),
            ),
            replace(
                LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH,
                planned_path_raster_mode=None,
            ),
        )
        for profile in profiles:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                RuidaJobCompiler(profile)

    def test_stationary_events_validate_duration_and_position(self) -> None:
        invalid_events = (
            (TravelTo(20, 20), Dwell(0), MarkTo(30, 20)),
            (TravelTo(20, 20), Pulse(0)),
            (Dwell(100), TravelTo(20, 20), MarkTo(30, 20)),
            (Pulse(100), TravelTo(20, 20)),
            (TravelTo(20, 20), Dwell(100)),
        )
        compiler = RuidaJobCompiler(LIGHTBURN_2103_644XS_STATIONARY_RESEARCH)
        for events in invalid_events:
            with self.subTest(events=events), self.assertRaises(ValueError):
                compiler.compile(JobPlan((_capability_vector_layer(events),)))

        pulse = compiler.compile(
            JobPlan(
                (_capability_vector_layer((TravelTo(20, 20), Pulse(100))),)
            )
        )
        self.assertEqual(pulse.bounds, Bounds(20, 20, 20, 20))
        self.assertEqual(pulse.marked_distance_mm, 0)

    def test_layer_control_values_are_strictly_validated(self) -> None:
        vector = _capability_vector_layer((TravelTo(20, 20), MarkTo(30, 20)))
        for frequency_hz in (True, 0, 20_000.0, 34_359_738_368):
            with (
                self.subTest(frequency_hz=frequency_hz),
                self.assertRaises(ValueError),
            ):
                RuidaJobCompiler(LIGHTBURN_2103_644XS_RF_RESEARCH).compile(
                    JobPlan((replace(vector, frequency_hz=frequency_hz),))
                )
        for pulse_width_ns in (True, -1, 100.0, 34_359_738_368):
            with (
                self.subTest(pulse_width_ns=pulse_width_ns),
                self.assertRaises(ValueError),
            ):
                RuidaJobCompiler(LIGHTBURN_2103_644XS_FIBER_RESEARCH).compile(
                    JobPlan((replace(vector, pulse_width_ns=pulse_width_ns),))
                )

        too_long = replace(
            vector,
            events=(
                TravelTo(20, 20),
                Dwell(34_359_738.368),
                MarkTo(30, 20),
            ),
        )
        with self.assertRaises(ValueError):
            RuidaJobCompiler(
                LIGHTBURN_2103_644XS_STATIONARY_RESEARCH
            ).compile(JobPlan((too_long,)))

        raster = _raster_layer((TravelTo(20, 20), MarkTo(30, 20)))
        with self.assertRaises(ValueError):
            RuidaJobCompiler(LIGHTBURN_2103_644XS_Z_RESEARCH).compile(
                JobPlan((replace(raster, z_offset_mm=2_147_483.648),))
            )

    def test_z_offsets_require_one_native_raster_layer(self) -> None:
        raster = _raster_layer((TravelTo(20, 20), MarkTo(30, 20)))
        compiler = RuidaJobCompiler(LIGHTBURN_2103_644XS_Z_RESEARCH)
        for offset in (0, 0.0004):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                compiler.compile(
                    JobPlan((replace(raster, z_offset_mm=offset),))
                )

        vector = replace(
            _baseline_plan().layers[0],
            z_offset_mm=1,
        )
        with self.assertRaisesRegex(
            UnsupportedJobFeatureError,
            "one native raster",
        ):
            compiler.compile(JobPlan((vector,)))
        with self.assertRaisesRegex(
            UnsupportedJobFeatureError,
            "one native raster",
        ):
            compiler.compile(
                JobPlan(
                    (
                        replace(raster, z_offset_mm=1),
                        replace(
                            raster,
                            index=1,
                            events=(
                                TravelTo(20, 21),
                                MarkTo(30, 21),
                            ),
                        ),
                    )
                )
            )

    def test_dynamic_power_requires_matching_layer_channels(self) -> None:
        channels = _capability_channels()
        changed_enabled = (
            LaserChannelPlan(1, False, 10, 10),
            LaserChannelPlan(2, True, 40, 40),
        )
        events = (
            TravelTo(20, 20),
            MarkWithPower(30, 20, channels),
        )
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        without_channels = replace(
            _baseline_plan().layers[0],
            events=events,
        )
        with self.assertRaisesRegex(
            UnsupportedJobFeatureError,
            "explicit layer laser channels",
        ):
            compiler.compile(JobPlan((without_channels,)))

        layer = _capability_vector_layer(
            (
                TravelTo(20, 20),
                MarkWithPower(30, 20, changed_enabled),
            )
        )
        with self.assertRaisesRegex(ValueError, "enable states"):
            compiler.compile(JobPlan((layer,)))

    def test_dynamic_power_only_lowers_laser1_maximum(self) -> None:
        layer_channels = (
            LaserChannelPlan(1, True, 10, 70),
            LaserChannelPlan(2, False, 30, 50),
        )
        valid_channels = (
            LaserChannelPlan(1, True, 10, 40),
            LaserChannelPlan(2, False, 30, 50),
        )
        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
        )
        valid = LayerPlan(
            index=0,
            speed_mm_s=10,
            min_power_percent=10,
            max_power_percent=70,
            events=(
                TravelTo(20, 20),
                MarkWithPower(30, 20, valid_channels),
            ),
            laser_channels=layer_channels,
        )
        result = compiler.compile(JobPlan((valid,)))
        cut_index = next(
            index
            for index, record in enumerate(result.program.records)
            if isinstance(record, KnownCommand)
            and record.name == "cut_absolute"
        )
        envelope = result.program.records[cut_index - 7 : cut_index]
        self.assertEqual(
            [
                record.name
                for record in envelope
                if isinstance(record, KnownCommand)
            ],
            [
                "layer_control",
                "select_layer",
                "laser_1_min_power",
                "laser_1_max_power",
                "laser_2_min_power",
                "laser_2_max_power",
                "external_io",
            ],
        )
        self.assertEqual(envelope[2].values["power_percent"], 10)
        self.assertEqual(envelope[3].values["power_percent"], 40)
        self.assertEqual(envelope[4].values["power_percent"], 30)
        self.assertEqual(envelope[5].values["power_percent"], 50)

        invalid_channels = (
            (
                LaserChannelPlan(1, True, 10, 100),
                LaserChannelPlan(2, False, 30, 50),
            ),
            layer_channels,
            (
                LaserChannelPlan(1, True, 11, 40),
                LaserChannelPlan(2, False, 30, 50),
            ),
            (
                LaserChannelPlan(1, True, 10, 40),
                LaserChannelPlan(2, False, 35, 45),
            ),
        )
        for channels in invalid_channels:
            layer = replace(
                valid,
                events=(
                    TravelTo(20, 20),
                    MarkWithPower(30, 20, channels),
                ),
            )
            with (
                self.subTest(channels=channels),
                self.assertRaises(UnsupportedJobFeatureError),
            ):
                compiler.compile(JobPlan((layer,)))

        enabled_laser_2 = (
            LaserChannelPlan(1, True, 10, 70),
            LaserChannelPlan(2, True, 30, 50),
        )
        with self.assertRaisesRegex(
            UnsupportedJobFeatureError,
            "Laser enable mask 3",
        ):
            compiler.compile(
                JobPlan(
                    (
                        replace(
                            valid,
                            laser_channels=enabled_laser_2,
                            events=(
                                TravelTo(20, 20),
                                MarkWithPower(
                                    30,
                                    20,
                                    enabled_laser_2,
                                ),
                            ),
                        ),
                    )
                )
            )

    def test_research_modes_have_no_execution_claim(self) -> None:
        modes = (
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
            .planned_path_raster_mode,
            LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH.laser_channel_mode,
            LIGHTBURN_2103_644XS_STATIONARY_RESEARCH.stationary_event_mode,
            LIGHTBURN_2103_644XS_RF_RESEARCH.layer_frequency_mode,
            LIGHTBURN_2103_644XS_FIBER_RESEARCH.fiber_pulse_width_mode,
            LIGHTBURN_2103_644XS_Z_RESEARCH.paired_z_offset_mode,
            LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH
            .dynamic_vector_power_mode,
        )
        for mode in modes:
            self.assertIsNotNone(mode)
            assert mode is not None
            self.assertEqual(mode.execution_evidence, "not-observed")

    def test_planned_path_raster_rejects_unsupported_mixes(self) -> None:
        events = _diagonal_45_unidirectional_events()
        layer = _planned_path_raster_plan(events).layers[0]
        cases = (
            replace(layer, events=events),
            replace(layer, scan_axis="horizontal"),
            replace(layer, raster_strategy="unidirectional"),
            replace(layer, raster_sections=()),
            replace(
                layer,
                raster_sections=(
                    RasterSection(
                        (
                            TravelTo(1, 1),
                            SetModulation(50),
                            MarkTo(2, 2),
                        )
                    ),
                ),
            ),
        )

        for invalid in cases:
            with self.subTest(layer=invalid):
                with self.assertRaises(
                    (ValueError, UnsupportedJobFeatureError)
                ):
                    RuidaJobCompiler(
                        LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
                    ).compile(JobPlan((invalid,)))

    def test_native_raster_rejects_planned_path_sections(self) -> None:
        layer = _raster_layer((TravelTo(0, 0), MarkTo(1, 0)))
        invalid = replace(
            layer,
            raster_sections=(RasterSection((TravelTo(0, 0), MarkTo(1, 1))),),
        )

        with self.assertRaisesRegex(ValueError, "Native raster"):
            RuidaJobCompiler().compile(JobPlan((invalid,)))

    def test_reported_metric_override_is_validated(self) -> None:
        valid = _planned_path_raster_plan(
            _diagonal_45_unidirectional_events(),
            reported_job_metric_mm=6,
        )
        result = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
        ).compile(valid)
        self.assertEqual(result.marked_distance_mm, 6)

        for value in (True, -1, float("nan"), 34_359_738_368):
            with self.subTest(value=value):
                plan = replace(valid, reported_job_metric_mm=value)
                with self.assertRaises(ValueError):
                    RuidaJobCompiler(
                        LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
                    ).compile(plan)

        invalid_bounds = replace(
            valid,
            declared_metadata_bounds=Bounds(2, 0, 1, 1),
        )
        with self.assertRaisesRegex(ValueError, "minimums"):
            RuidaJobCompiler(
                LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
            ).compile(invalid_bounds)

    def test_profile_must_support_planned_path_raster(self) -> None:
        plan = _planned_path_raster_plan(_diagonal_45_unidirectional_events())

        with self.assertRaisesRegex(
            UnsupportedJobFeatureError,
            "not supported by this job profile",
        ):
            RuidaJobCompiler().compile(plan)

        compiler = RuidaJobCompiler(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
        )
        native = _raster_layer((TravelTo(20, 20), MarkTo(30, 20)))
        vector = _baseline_plan().layers[0]
        for layer in (native, vector):
            with (
                self.subTest(layer=layer),
                self.assertRaises(UnsupportedJobFeatureError),
            ):
                compiler.compile(JobPlan((layer,)))

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
            "fixtures/hardware/ruida-644xs-usb-serial-v1/manifest-v1.json",
        )
        self.assertTrue(
            (ROOT / LIGHTBURN_2103_644XS.execution_evidence_source).is_file()
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
        self.assertIsNone(LIGHTBURN_2103_644XS.planned_path_raster_mode)
        self.assertEqual(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH.execution_evidence,
            "not-observed",
        )
        self.assertIsNone(
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
            .execution_evidence_source
        )
        planned_path = (
            LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH
            .planned_path_raster_mode
        )
        self.assertIsNotNone(planned_path)
        assert planned_path is not None
        self.assertEqual(planned_path.layer_mode, 0)
        self.assertEqual(planned_path.layer_operation, 0)
        self.assertEqual(planned_path.section_separator_operation, 5)
        self.assertEqual(
            planned_path.semantic_evidence,
            "controlled-offline-fixture",
        )
        self.assertEqual(
            planned_path.evidence_source,
            "LightBurn 2.1.03 capability fixtures c002 through c005",
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
