"""Strict JSON helpers for versioned interchange documents."""

from __future__ import annotations

import json
import math
from decimal import Decimal, DecimalException
from typing import Any


MAX_SAFE_INTEGER = (1 << 53) - 1
MIN_SAFE_INTEGER = -MAX_SAFE_INTEGER
_MAX_SAFE_DECIMAL = Decimal(MAX_SAFE_INTEGER)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate JSON object key: {name!r}")
        result[name] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _parsed_integer(value: str) -> int:
    digits = value.removeprefix("-")
    limit = str(MAX_SAFE_INTEGER)
    if len(digits) > len(limit) or (
        len(digits) == len(limit) and digits > limit
    ):
        raise ValueError(
            "JSON integer is outside the interoperable numeric range"
        )
    return int(value)


def _parsed_decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except DecimalException as error:
        raise ValueError(
            "JSON number is outside the interoperable numeric range"
        ) from error
    if (
        not result.is_finite()
        or result.copy_abs() > _MAX_SAFE_DECIMAL
    ):
        raise ValueError(
            "JSON number is outside the interoperable numeric range"
        )
    return result


def loads(value: str) -> Any:
    """Parse standards-compliant JSON and reject duplicate object keys."""
    return json.loads(
        value,
        object_pairs_hook=_object,
        parse_constant=_constant,
        parse_float=_parsed_decimal,
        parse_int=_parsed_integer,
    )


def integer(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Normalize a JSON Schema integer-valued number to a Python int."""
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, Decimal),
    ):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, float):
        valid = math.isfinite(value) and value.is_integer()
    elif isinstance(value, Decimal):
        valid = value.is_finite() and value == value.to_integral_value()
    else:
        valid = True
    if not valid:
        raise ValueError(f"{label} must be an integer")
    if value < MIN_SAFE_INTEGER or value > MAX_SAFE_INTEGER:
        raise ValueError(
            f"{label} must be between {MIN_SAFE_INTEGER} and "
            f"{MAX_SAFE_INTEGER}"
        )
    result = int(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return result


def number(value: Any, label: str) -> int | float:
    """Normalize a bounded JSON number to its binary64 representation."""
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, Decimal),
    ):
        raise ValueError(f"{label} must be a finite number")
    if isinstance(value, int):
        if MIN_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            return value
        raise ValueError(
            f"{label} must be between {MIN_SAFE_INTEGER} and "
            f"{MAX_SAFE_INTEGER}"
        )
    if isinstance(value, float):
        if (
            not math.isfinite(value)
            or value < MIN_SAFE_INTEGER
            or value > MAX_SAFE_INTEGER
        ):
            raise ValueError(f"{label} must be a finite number")
        return value
    if (
        not value.is_finite()
        or value < MIN_SAFE_INTEGER
        or value > MAX_SAFE_INTEGER
    ):
        raise ValueError(f"{label} must be a finite number")
    if value == value.to_integral_value():
        return int(value)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result
