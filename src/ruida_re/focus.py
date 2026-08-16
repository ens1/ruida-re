"""Evidence-labelled Ruida focus and position protocol candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from .api import RuidaCodec
from .registry import get_registry

FOCUS_DEPTH_ADDRESS = 0x010E
CURRENT_X_ADDRESS = 0x0221
CURRENT_Y_ADDRESS = 0x0231
CURRENT_Z_ADDRESS = 0x0241


def _validate_u35(value: int) -> None:
    if type(value) is not int or not 0 <= value < 1 << 35:
        raise ValueError("Raw setting value must fit an unsigned 35-bit value")


@dataclass(frozen=True)
class FocusDepthReading:
    """Raw reply from the implementation-reported focus-depth address."""

    raw_value: int
    address: ClassVar[int] = FOCUS_DEPTH_ADDRESS
    semantic_evidence: ClassVar[Literal["reported"]] = "reported"
    unit_evidence: ClassVar[Literal["simulator-only"]] = "simulator-only"
    unit_hypothesis: ClassVar[Literal["unsigned-u35-micrometres"]] = (
        "unsigned-u35-micrometres"
    )

    def __post_init__(self) -> None:
        _validate_u35(self.raw_value)

    @property
    def hypothesized_mm(self) -> float:
        """Apply an unvalidated micrometre hypothesis for analysis only."""
        return self.raw_value / 1000.0


@dataclass(frozen=True)
class _CurrentPositionReading:
    """Raw reply with an explicitly hypothetical signed-mm conversion."""

    raw_value: int
    axis: ClassVar[str]
    address: ClassVar[int]
    semantic_evidence: ClassVar[Literal["reported"]] = "reported"
    unit_evidence: ClassVar[Literal["reported"]] = "reported"
    unit_hypothesis: ClassVar[Literal["signed-35-bit-micrometres"]] = (
        "signed-35-bit-micrometres"
    )

    def __post_init__(self) -> None:
        _validate_u35(self.raw_value)

    @property
    def hypothesized_micrometres(self) -> int:
        """Apply the implementation-reported signed 35-bit interpretation."""
        value = self.raw_value
        if value >= 1 << 34:
            value -= 1 << 35
        return value

    @property
    def hypothesized_mm(self) -> float:
        """Apply the implementation-reported micrometre scale."""
        return self.hypothesized_micrometres / 1000.0


@dataclass(frozen=True)
class CurrentXReading(_CurrentPositionReading):
    """Raw value read from the reported current-X address."""

    axis: ClassVar[Literal["x"]] = "x"
    address: ClassVar[int] = CURRENT_X_ADDRESS


@dataclass(frozen=True)
class CurrentYReading(_CurrentPositionReading):
    """Raw value read from the reported current-Y address."""

    axis: ClassVar[Literal["y"]] = "y"
    address: ClassVar[int] = CURRENT_Y_ADDRESS


@dataclass(frozen=True)
class CurrentZReading(_CurrentPositionReading):
    """Raw value read from the reported current-Z address."""

    axis: ClassVar[Literal["z"]] = "z"
    address: ClassVar[int] = CURRENT_Z_ADDRESS


@dataclass(frozen=True)
class AutofocusCandidate:
    """Offline-only descriptor for the reported D8 2E command candidate."""

    logical: bytes
    name: Literal["focus_z"]
    shape_evidence: Literal["reported"]
    semantic_evidence: Literal["reported"]
    controller_effect: Literal["unknown"]
    reply_behavior: Literal["unknown"]
    evidence_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.logical != bytes.fromhex("d82e"):
            raise ValueError("Autofocus candidate must be logical D8 2E")


def build_autofocus_candidate(*, magic: int = 0x88) -> AutofocusCandidate:
    """Build D8 2E offline without granting it a live exchange contract."""
    codec = RuidaCodec(magic=magic, context="request")
    command = codec.command("focus_z")
    program = codec.program([command])
    spec = get_registry("request").name(command.name)
    if spec is None:
        raise AssertionError("Focus command is absent from the registry")
    logical = codec.encode(program)
    return AutofocusCandidate(
        logical=logical,
        name="focus_z",
        shape_evidence="reported",
        semantic_evidence="reported",
        controller_effect="unknown",
        reply_behavior="unknown",
        evidence_sources=tuple(
            sorted(set(spec.shape_sources) | set(spec.semantic_sources))
        ),
    )


__all__ = (
    "CURRENT_X_ADDRESS",
    "CURRENT_Y_ADDRESS",
    "CURRENT_Z_ADDRESS",
    "FOCUS_DEPTH_ADDRESS",
    "AutofocusCandidate",
    "CurrentXReading",
    "CurrentYReading",
    "CurrentZReading",
    "FocusDepthReading",
    "build_autofocus_candidate",
)
