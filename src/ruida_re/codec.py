"""Pure value and scrambling codecs used by Ruida job files."""

from __future__ import annotations

import math


MICROMETERS_PER_MM = 1000.0
POWER_FULL_SCALE = 0x3FFF


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_byte(value: object, label: str) -> int:
    if not _is_integer(value):
        raise ValueError(f"{label} must be between 0 and 255")
    result = int(value)
    if not 0 <= result <= 0xFF:
        raise ValueError(f"{label} must be between 0 and 255")
    return result


def _require_finite(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def swizzle_byte(value: int, magic: int = 0x88) -> int:
    """Encode one Ruida byte using the controller's byte transform."""
    value = _require_byte(value, "Byte value")
    magic = _require_byte(magic, "Magic value")
    value ^= (value >> 7) & 0xFF
    value ^= (value << 7) & 0xFF
    value ^= (value >> 7) & 0xFF
    value ^= magic
    return (value + 1) & 0xFF


def unswizzle_byte(value: int, magic: int = 0x88) -> int:
    """Decode one scrambled Ruida byte."""
    value = _require_byte(value, "Byte value")
    magic = _require_byte(magic, "Magic value")
    value = (value - 1) & 0xFF
    value ^= magic
    value ^= (value >> 7) & 0xFF
    value ^= (value << 7) & 0xFF
    value ^= (value >> 7) & 0xFF
    return value


def swizzle(data: bytes, magic: int = 0x88) -> bytes:
    """Encode a complete Ruida command stream."""
    _require_byte(magic, "Magic value")
    return bytes(swizzle_byte(value, magic) for value in data)


def unswizzle(data: bytes, magic: int = 0x88) -> bytes:
    """Decode a complete Ruida command stream."""
    _require_byte(magic, "Magic value")
    return bytes(unswizzle_byte(value, magic) for value in data)


def _encode_base128(value: int, width: int) -> bytes:
    limit = 1 << (width * 7)
    if not _is_integer(value) or not 0 <= value < limit:
        raise ValueError(f"Value must be between 0 and {limit - 1}")
    return bytes(
        (value >> shift) & 0x7F
        for shift in range((width - 1) * 7, -1, -7)
    )


def _decode_base128(data: bytes, width: int) -> int:
    if len(data) != width:
        raise ValueError(
            f"A {width * 7}-bit value requires exactly {width} bytes"
        )
    if any(value & 0x80 for value in data):
        raise ValueError("Base-128 digits must be seven-bit bytes")
    value = 0
    for item in data:
        value = (value << 7) | item
    return value


def encode_u14(value: int) -> bytes:
    """Encode an unsigned integer as two seven-bit bytes."""
    return _encode_base128(value, 2)


def encode_s14(value: int) -> bytes:
    """Encode a signed integer as two seven-bit bytes."""
    if not _is_integer(value) or not -(1 << 13) <= value < (1 << 13):
        raise ValueError("Value must fit in a signed 14-bit integer")
    return encode_u14(value & 0x3FFF)


def decode_u14(data: bytes) -> int:
    """Decode an unsigned two-byte, 14-bit value."""
    return _decode_base128(data, 2)


def decode_s14(data: bytes) -> int:
    """Decode a signed integer from two seven-bit bytes."""
    value = decode_u14(data)
    if value >= 1 << 13:
        value -= 1 << 14
    return value


def encode_u35(value: int) -> bytes:
    """Encode an unsigned integer as five seven-bit bytes."""
    return _encode_base128(value, 5)


def encode_s35(value: int) -> bytes:
    """Encode a signed integer as five seven-bit bytes."""
    if not _is_integer(value) or not -(1 << 34) <= value < (1 << 34):
        raise ValueError("Value must fit in a signed 35-bit integer")
    return encode_u35(value & ((1 << 35) - 1))


def decode_u35(data: bytes) -> int:
    """Decode an unsigned five-byte, 35-bit value."""
    return _decode_base128(data, 5)


def decode_s35(data: bytes) -> int:
    """Decode a signed integer from five seven-bit bytes."""
    value = decode_u35(data)
    if value >= 1 << 34:
        value -= 1 << 35
    return value


def encode_s32(value: int) -> bytes:
    """Encode a signed 32-bit value in five seven-bit groups."""
    if not _is_integer(value) or not -(1 << 31) <= value < (1 << 31):
        raise ValueError("Value must fit in a signed 32-bit integer")
    return encode_u35(value & 0xFFFFFFFF)


def decode_s32(data: bytes) -> int:
    """Decode a signed 32-bit value carried in five groups."""
    value = decode_u35(data)
    padding = value >> 32
    low = value & 0xFFFFFFFF
    if padding not in (0, 0x7):
        raise ValueError("Invalid padding for a signed 32-bit value")
    if padding == 0x7 and low < 1 << 31:
        raise ValueError("Sign-extended padding requires a negative value")
    if low >= 1 << 31:
        low -= 1 << 32
    return low


def encode_power(percent: float) -> bytes:
    """Encode a percentage using the scale emitted by LightBurn."""
    percent = _require_finite(percent, "Power")
    if not 0.0 <= percent <= 100.0:
        raise ValueError("Power must be between 0 and 100 percent")
    scaled = percent * POWER_FULL_SCALE / 100.0
    value = int(scaled + 0.5)
    return encode_u14(value)


def decode_power(data: bytes) -> float:
    """Decode a Ruida U14 power value as a percentage."""
    return decode_u14(data) * 100.0 / POWER_FULL_SCALE


def encode_mm(value: float) -> bytes:
    """Encode millimeters as a signed 32-bit value in five groups."""
    value = _require_finite(value, "Millimeter value")
    return encode_s32(round(value * MICROMETERS_PER_MM))


def decode_mm(data: bytes) -> float:
    """Decode a signed coordinate or speed into millimeters."""
    return decode_s32(data) / MICROMETERS_PER_MM


def encode_xy(x: float, y: float) -> bytes:
    """Encode two absolute coordinates."""
    return encode_mm(x) + encode_mm(y)


def decode_xy(data: bytes) -> tuple[float, float]:
    """Decode two signed 32-bit coordinates in five groups each."""
    if len(data) != 10:
        raise ValueError("An absolute XY pair requires exactly ten bytes")
    return decode_mm(data[:5]), decode_mm(data[5:])
