"""Public package surface for planned-job compilation."""

from __future__ import annotations

import unittest

import ruida_re


PUBLIC_JOB_NAMES = (
    "Bounds",
    "CompileResult",
    "Dwell",
    "DynamicVectorPowerMode",
    "FiberPulseWidthMode",
    "JobPlan",
    "LIGHTBURN_2103_644XS",
    "LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH",
    "LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH",
    "LIGHTBURN_2103_644XS_FIBER_RESEARCH",
    "LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH",
    "LIGHTBURN_2103_644XS_RF_RESEARCH",
    "LIGHTBURN_2103_644XS_STATIONARY_RESEARCH",
    "LIGHTBURN_2103_644XS_Z_RESEARCH",
    "LaserChannelMapping",
    "LaserChannelMode",
    "LaserChannelPlan",
    "LayerEvent",
    "LayerFrequencyMode",
    "LayerKind",
    "LayerPlan",
    "MarkTo",
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


class JobPublicApiTest(unittest.TestCase):
    def test_job_compiler_symbols_are_package_exports(self) -> None:
        for name in PUBLIC_JOB_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, ruida_re.__all__)
                self.assertTrue(hasattr(ruida_re, name))

    def test_public_exports_are_unique(self) -> None:
        self.assertEqual(
            len(ruida_re.__all__),
            len(set(ruida_re.__all__)),
        )


if __name__ == "__main__":
    unittest.main()
