"""Tests for evidence-labelled focus and position candidates."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from ruida_re import (
    CURRENT_X_ADDRESS,
    CURRENT_Y_ADDRESS,
    CURRENT_Z_ADDRESS,
    FOCUS_DEPTH_ADDRESS,
    CurrentXReading,
    CurrentYReading,
    CurrentZReading,
    FocusDepthReading,
    RuidaCodec,
    build_autofocus_candidate,
)


class FocusProtocolTest(unittest.TestCase):
    def test_reported_addresses_encode_to_exact_logical_requests(self) -> None:
        codec = RuidaCodec(context="request")
        cases = (
            (FOCUS_DEPTH_ADDRESS, "da00020e"),
            (CURRENT_X_ADDRESS, "da000421"),
            (CURRENT_Y_ADDRESS, "da000431"),
            (CURRENT_Z_ADDRESS, "da000441"),
        )
        for address, expected in cases:
            with self.subTest(address=address):
                command = codec.command("get_setting", address=address)
                logical = codec.encode_commands([command])
                self.assertEqual(logical, bytes.fromhex(expected))

    def test_focus_depth_preserves_raw_value_and_labels_hypothesis(self) -> None:
        reading = FocusDepthReading(5000)

        self.assertEqual(reading.raw_value, 5000)
        self.assertEqual(reading.address, 0x010E)
        self.assertEqual(reading.semantic_evidence, "reported")
        self.assertEqual(reading.unit_evidence, "simulator-only")
        self.assertEqual(
            reading.unit_hypothesis,
            "unsigned-u35-micrometres",
        )
        self.assertEqual(reading.hypothesized_mm, 5.0)
        with self.assertRaises(FrozenInstanceError):
            setattr(reading, "raw_value", 0)

    def test_position_readings_preserve_raw_and_signed_hypothesis(self) -> None:
        raw_negative = (1 << 35) - 18_200
        cases = (
            (CurrentXReading(12_300), "x", 0x0221, 12.3),
            (CurrentYReading(45_600), "y", 0x0231, 45.6),
            (CurrentZReading(raw_negative), "z", 0x0241, -18.2),
        )
        for reading, axis, address, hypothesized_mm in cases:
            with self.subTest(axis=axis):
                self.assertEqual(reading.axis, axis)
                self.assertEqual(reading.address, address)
                self.assertEqual(reading.semantic_evidence, "reported")
                self.assertEqual(reading.unit_evidence, "reported")
                self.assertEqual(
                    reading.hypothesized_mm,
                    hypothesized_mm,
                )

    def test_readings_reject_values_outside_u35(self) -> None:
        for value in (-1, 1 << 35, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FocusDepthReading(value)

    def test_autofocus_builder_is_an_offline_unknown_behavior_candidate(
        self,
    ) -> None:
        candidate = build_autofocus_candidate()

        self.assertEqual(candidate.logical, bytes.fromhex("d82e"))
        self.assertEqual(candidate.name, "focus_z")
        self.assertEqual(candidate.shape_evidence, "reported")
        self.assertEqual(candidate.semantic_evidence, "reported")
        self.assertEqual(candidate.controller_effect, "unknown")
        self.assertEqual(candidate.reply_behavior, "unknown")
        self.assertEqual(len(candidate.evidence_sources), 2)

    def test_autofocus_candidate_validates_magic_without_using_it_as_data(
        self,
    ) -> None:
        self.assertEqual(
            build_autofocus_candidate(magic=0).logical,
            bytes.fromhex("d82e"),
        )
        for magic in (-1, 256, True):
            with self.subTest(magic=magic), self.assertRaises(ValueError):
                build_autofocus_candidate(magic=magic)


if __name__ == "__main__":
    unittest.main()
