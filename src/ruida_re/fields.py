"""Composable field codecs for Ruida command payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codec import (
    decode_mm,
    decode_power,
    decode_s14,
    decode_s32,
    decode_s35,
    decode_u14,
    decode_u35,
    encode_mm,
    encode_power,
    encode_s14,
    encode_s32,
    encode_s35,
    encode_u14,
    encode_u35,
)


class FieldError(ValueError):
    """Raised when a command field cannot be decoded or encoded."""


@dataclass(frozen=True)
class Field:
    """Base class for one named command field."""

    name: str

    def decode(self, data: bytes, offset: int) -> tuple[Any, int]:
        raise NotImplementedError

    def encode(self, value: Any) -> bytes:
        raise NotImplementedError

    @staticmethod
    def take(data: bytes, offset: int, size: int) -> bytes:
        end = offset + size
        if end > len(data):
            raise FieldError(
                f"Need {size} bytes at offset {offset}, "
                f"but only {len(data) - offset} remain"
            )
        return data[offset:end]

    @classmethod
    def take_groups(cls, data: bytes, offset: int, size: int) -> bytes:
        raw = cls.take(data, offset, size)
        if any(value & 0x80 for value in raw):
            raise FieldError(
                f"Base-128 field at offset {offset} contains a high bit"
            )
        return raw


@dataclass(frozen=True)
class ByteField(Field):
    """One unsigned seven-bit protocol byte."""

    def decode(self, data: bytes, offset: int) -> tuple[int, int]:
        raw = self.take_groups(data, offset, 1)
        return raw[0], offset + 1

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, int) or not 0 <= value <= 0x7F:
            raise FieldError(f"{self.name} must be an integer from 0 to 127")
        return bytes((value,))


@dataclass(frozen=True)
class S7Field(Field):
    """Signed integer stored in one seven-bit group."""

    def decode(self, data: bytes, offset: int) -> tuple[int, int]:
        raw = self.take_groups(data, offset, 1)[0]
        if raw >= 1 << 6:
            raw -= 1 << 7
        return raw, offset + 1

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, int) or not -(1 << 6) <= value < (1 << 6):
            raise FieldError(f"{self.name} must fit a signed 7-bit integer")
        return bytes((value & 0x7F,))


@dataclass(frozen=True)
class U14Field(Field):
    """Unsigned integer stored in two seven-bit groups."""

    def decode(self, data: bytes, offset: int) -> tuple[int, int]:
        raw = self.take_groups(data, offset, 2)
        return decode_u14(raw), offset + 2

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, int):
            raise FieldError(f"{self.name} must be an integer")
        return encode_u14(value)


@dataclass(frozen=True)
class U35Field(Field):
    """Unsigned integer stored in five seven-bit groups."""

    def decode(self, data: bytes, offset: int) -> tuple[int, int]:
        raw = self.take_groups(data, offset, 5)
        return decode_u35(raw), offset + 5

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, int):
            raise FieldError(f"{self.name} must be an integer")
        return encode_u35(value)


@dataclass(frozen=True)
class AbsoluteMmField(Field):
    """Millimeter value stored as signed 32-bit micrometers."""

    def decode(self, data: bytes, offset: int) -> tuple[float, int]:
        raw = self.take_groups(data, offset, 5)
        try:
            return decode_mm(raw), offset + 5
        except ValueError as error:
            raise FieldError(str(error)) from error

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, (int, float)):
            raise FieldError(f"{self.name} must be numeric")
        return encode_mm(float(value))


@dataclass(frozen=True)
class RelativeMmField(Field):
    """Relative millimeter value stored as signed U14 micrometers."""

    def decode(self, data: bytes, offset: int) -> tuple[float, int]:
        raw = self.take_groups(data, offset, 2)
        value = decode_s14(raw)
        return value / 1000.0, offset + 2

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, (int, float)):
            raise FieldError(f"{self.name} must be numeric")
        micrometers = round(float(value) * 1000.0)
        try:
            return encode_s14(micrometers)
        except ValueError as error:
            raise FieldError(str(error)) from error


@dataclass(frozen=True)
class S35Field(Field):
    """Signed integer stored in five seven-bit groups."""

    def decode(self, data: bytes, offset: int) -> tuple[int, int]:
        raw = self.take_groups(data, offset, 5)
        return decode_s35(raw), offset + 5

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, int):
            raise FieldError(f"{self.name} must be an integer")
        return encode_s35(value)


@dataclass(frozen=True)
class PowerField(Field):
    """Power percentage stored as a scaled U14 value."""

    def decode(self, data: bytes, offset: int) -> tuple[float, int]:
        raw = self.take_groups(data, offset, 2)
        return decode_power(raw), offset + 2

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, (int, float)):
            raise FieldError(f"{self.name} must be numeric")
        return encode_power(float(value))


@dataclass(frozen=True)
class ScaledS32Field(Field):
    """Signed 32-bit value in five groups with a numeric scale."""

    scale: float = 1000.0

    def decode(self, data: bytes, offset: int) -> tuple[float, int]:
        raw = self.take_groups(data, offset, 5)
        try:
            return decode_s32(raw) / self.scale, offset + 5
        except ValueError as error:
            raise FieldError(str(error)) from error

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, (int, float)):
            raise FieldError(f"{self.name} must be numeric")
        return encode_s32(round(float(value) * self.scale))


@dataclass(frozen=True)
class ScaledU35Field(Field):
    """Unsigned five-group value exposed with a numeric scale."""

    scale: float = 1000.0

    def decode(self, data: bytes, offset: int) -> tuple[float, int]:
        raw = self.take_groups(data, offset, 5)
        return decode_u35(raw) / self.scale, offset + 5

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, (int, float)):
            raise FieldError(f"{self.name} must be numeric")
        scaled = round(float(value) * self.scale)
        if scaled < 0:
            raise FieldError(f"{self.name} cannot be negative")
        return encode_u35(scaled)


@dataclass(frozen=True)
class ColorField(Field):
    """RGB color stored as a five-group BGR integer."""

    def decode(self, data: bytes, offset: int) -> tuple[int, int]:
        raw = decode_u35(self.take_groups(data, offset, 5))
        value = ((raw & 0xFF) << 16) | (raw & 0xFF00)
        value |= (raw >> 16) & 0xFF
        return value, offset + 5

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, int) or not 0 <= value <= 0xFFFFFF:
            raise FieldError(f"{self.name} must be a 24-bit RGB integer")
        bgr = ((value & 0xFF) << 16) | (value & 0xFF00)
        bgr |= (value >> 16) & 0xFF
        return encode_u35(bgr)


@dataclass(frozen=True)
class BytesField(Field):
    """Opaque fixed-width seven-bit bytes represented as hexadecimal."""

    size: int

    def decode(self, data: bytes, offset: int) -> tuple[str, int]:
        raw = self.take_groups(data, offset, self.size)
        return raw.hex(), offset + self.size

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, str):
            raise FieldError(f"{self.name} must be a hexadecimal string")
        try:
            raw = bytes.fromhex(value)
        except ValueError as error:
            raise FieldError(f"Invalid hexadecimal {self.name}") from error
        if len(raw) != self.size:
            raise FieldError(
                f"{self.name} must contain exactly {self.size} bytes"
            )
        if any(value & 0x80 for value in raw):
            raise FieldError(f"{self.name} must contain seven-bit bytes")
        return raw


@dataclass(frozen=True)
class CStringField(Field):
    """Null-terminated bytes represented as lowercase hexadecimal."""

    def decode(self, data: bytes, offset: int) -> tuple[str, int]:
        end = data.find(b"\x00", offset)
        if end < 0:
            raise FieldError(f"Unterminated {self.name} at offset {offset}")
        raw = self.take_groups(data, offset, end - offset)
        return raw.hex(), end + 1

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, str):
            raise FieldError(f"{self.name} must be a hexadecimal string")
        try:
            raw = bytes.fromhex(value)
        except ValueError as error:
            raise FieldError(f"Invalid hexadecimal {self.name}") from error
        if b"\x00" in raw:
            raise FieldError(f"{self.name} cannot contain a null byte")
        if any(value & 0x80 for value in raw):
            raise FieldError(f"{self.name} must contain seven-bit bytes")
        return raw + b"\x00"


@dataclass(frozen=True)
class PackedBytes8Field(Field):
    """Eight bytes packed into two U35 values and represented as hex."""

    def decode(self, data: bytes, offset: int) -> tuple[str, int]:
        raw = self.take_groups(data, offset, 10)
        first_value = decode_u35(raw[:5])
        second_value = decode_u35(raw[5:])
        if first_value > 0xFFFFFFFF or second_value > 0xFFFFFFFF:
            raise FieldError("Packed byte groups contain nonzero padding")
        first = first_value.to_bytes(4, "big")
        second = second_value.to_bytes(4, "big")
        return (first + second).hex(), offset + 10

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, str):
            raise FieldError(f"{self.name} must be a hexadecimal string")
        try:
            raw = bytes.fromhex(value)
        except ValueError as error:
            raise FieldError(f"Invalid hexadecimal {self.name}") from error
        if len(raw) != 8:
            raise FieldError(f"{self.name} must contain exactly eight bytes")
        first = int.from_bytes(raw[:4], "big")
        second = int.from_bytes(raw[4:], "big")
        return encode_u35(first) + encode_u35(second)
