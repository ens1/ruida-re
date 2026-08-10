"""Generate the versioned, language-neutral command catalog."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from .cli_io import atomic_write_text
from .fields import (
    AbsoluteMmField,
    ByteField,
    BytesField,
    ColorField,
    CStringField,
    Field,
    PackedBytes8Field,
    PowerField,
    RelativeMmField,
    S7Field,
    S35Field,
    ScaledS32Field,
    ScaledU35Field,
    U14Field,
    U35Field,
)
from .program import SCHEMA as PROGRAM_SCHEMA
from .registry import (
    CATALOG_SOURCES,
    REGISTRIES,
    REGISTRY_CONTEXT_EVIDENCE,
    SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
    SRC_LIBLASERCUT,
    SRC_LIGHTBURN,
    SRC_LIGHTBURN_CAPABILITIES,
    SRC_MEERK40T,
    SRC_RUIDA_LASER,
    SRC_RUIDA_PA,
)
from .specs import CommandSpec

CATALOG_SCHEMA = "ruida-re.catalog.v1"
CATALOG_CONTEXTS = ("job", "request", "reply")


_SOURCE_PROVENANCE = (
    {
        "id": SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
        "kind": "local-fixture",
        "url": None,
        "revision": "v1",
        "license": None,
        "local_path": (
            "fixtures/hardware/ruida-644xs-usb-serial-v1/"
            "manifest-v1.json"
        ),
    },
    {
        "id": SRC_LIGHTBURN,
        "kind": "local-fixture",
        "url": None,
        "revision": "2.1.03",
        "license": None,
        "local_path": "fixtures/lightburn-2.1.03",
    },
    {
        "id": SRC_LIGHTBURN_CAPABILITIES,
        "kind": "local-fixture",
        "url": None,
        "revision": "2.1.03-capabilities-v1",
        "license": None,
        "local_path": (
            "fixtures/lightburn-2.1.03/capabilities/"
            "capabilities.json"
        ),
    },
    {
        "id": SRC_LIBLASERCUT,
        "kind": "comparison-oracle",
        "url": (
            "https://github.com/t-oster/LibLaserCut/tree/"
            "ebe72ea3af3b2ab52d797d8100c635f68722100e"
        ),
        "revision": "ebe72ea3af3b2ab52d797d8100c635f68722100e",
        "license": "LGPL-3.0-or-later",
    },
    {
        "id": SRC_MEERK40T,
        "kind": "permissive-reference",
        "url": (
            "https://github.com/meerk40t/meerk40t/tree/"
            "5f68a45bff41d98e4d3fe8b8267857218099afa8"
        ),
        "revision": "5f68a45bff41d98e4d3fe8b8267857218099afa8",
        "license": "MIT",
    },
    {
        "id": SRC_RUIDA_LASER,
        "kind": "comparison-oracle",
        "url": (
            "https://github.com/jnweiger/ruida-laser/tree/"
            "a1e7b9b93b10d5cac79c875bc3efec46f7397a11"
        ),
        "revision": "a1e7b9b93b10d5cac79c875bc3efec46f7397a11",
        "license": "GPL-2.0-only",
    },
    {
        "id": SRC_RUIDA_PA,
        "kind": "permissive-reference",
        "url": (
            "https://github.com/StevenIsaacs/ruida-pa/tree/"
            "92efde98004d9948474eb712ef6f5b164f468c4f"
        ),
        "revision": "92efde98004d9948474eb712ef6f5b164f468c4f",
        "license": "MIT",
    },
)


def _integer_json(minimum: int, maximum: int) -> dict[str, Any]:
    return {
        "kind": "integer",
        "unit": "unitless",
        "canonical_form": "json-integer",
        "minimum": minimum,
        "maximum": maximum,
    }


def _number_json(
    unit: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "finite-number",
        "unit": unit,
        "canonical_form": "finite-json-number",
        "finite": True,
        "arithmetic": "ieee-754-binary64",
    }
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


def _hex_json(
    length_kind: str,
    *,
    value: int | None = None,
    parameter: str | None = None,
    byte_minimum: int = 0,
    byte_maximum: int = 0xFF,
) -> dict[str, Any]:
    length: dict[str, Any] = {"kind": length_kind}
    if value is not None:
        length["value"] = value
    if parameter is not None:
        length["parameter"] = parameter
    return {
        "kind": "lowercase-hex-bytes",
        "unit": "bytes",
        "canonical_form": "lowercase-even-length-hex",
        "pattern": "^(?:[0-9a-f]{2})*$",
        "byte_length": length,
        "byte_minimum": byte_minimum,
        "byte_maximum": byte_maximum,
    }


def _integer_wire(
    carrier_bits: int,
    value_bits: int,
    representation: str,
    encoder_padding: str = "not-applicable",
    decoder_padding: str = "not-applicable",
) -> dict[str, Any]:
    return {
        "kind": "base128-integer",
        "group_bits": 7,
        "group_order": "most-significant-first",
        "group_high_bit": "zero",
        "carrier_bits": carrier_bits,
        "value_bits": value_bits,
        "representation": representation,
        "encoder_padding": encoder_padding,
        "decoder_padding": decoder_padding,
    }


def _numeric_conversion(
    kind: str,
    minimum: int,
    maximum: int,
    encode_operation: str,
    decode_operation: str,
    rounding: str,
    range_check: str,
    **values: Any,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "wire_integer_minimum": minimum,
        "wire_integer_maximum": maximum,
        "encode_operation": encode_operation,
        "decode_operation": decode_operation,
        "rounding": rounding,
        "encoder_range_check": range_check,
        **values,
    }


def _constant_scale(value: int | float) -> dict[str, Any]:
    return {
        "kind": "constant",
        "value": value,
        "meaning": "wire-units-per-json-unit",
    }


def _parameter_scale() -> dict[str, Any]:
    return {
        "kind": "field-parameter",
        "parameter": "scale",
        "meaning": "wire-units-per-json-unit",
    }


def _codec(
    identifier: str,
    json_type: str,
    wire_groups: int | None,
    parameters: tuple[str, ...],
    description: str,
    json_semantics: dict[str, Any],
    wire_semantics: dict[str, Any],
    conversion: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": identifier,
        "json_type": json_type,
        "wire_groups": wire_groups,
        "parameters": list(parameters),
        "description": description,
        "semantics": {
            "json": json_semantics,
            "wire": wire_semantics,
            "conversion": conversion,
        },
    }


_S32_MIN = -(1 << 31)
_S32_MAX = (1 << 31) - 1
_S35_MIN = -(1 << 34)
_S35_MAX = (1 << 34) - 1
_U35_MAX = (1 << 35) - 1


_CODEC_DEFINITIONS = (
    _codec(
        "absolute-mm-s32",
        "number",
        5,
        (),
        "Signed 32-bit micrometers in five base-128 groups, exposed as mm.",
        _number_json("mm", -2147483.648, 2147483.647),
        _integer_wire(
            35,
            32,
            "twos-complement",
            "zero",
            "zero-or-negative-sign-extension",
        ),
        _numeric_conversion(
            "scaled-integer",
            _S32_MIN,
            _S32_MAX,
            "round-json-times-scale",
            "wire-divided-by-scale",
            "nearest-ties-to-even",
            "after-rounding",
            scale=_constant_scale(1000),
        ),
    ),
    _codec(
        "bytes-7bit",
        "string",
        None,
        ("size",),
        "Fixed-size seven-bit bytes exposed as lowercase hexadecimal.",
        _hex_json(
            "field-parameter",
            parameter="size",
            byte_maximum=0x7F,
        ),
        {
            "kind": "seven-bit-byte-string",
            "group_bits": 7,
            "group_order": "source-order",
            "group_high_bit": "zero",
            "group_count": {
                "kind": "field-parameter",
                "parameter": "size",
            },
            "encoder_padding": "not-applicable",
            "decoder_padding": "not-applicable",
        },
        {
            "kind": "identity-bytes",
            "encode_operation": "hex-decode",
            "decode_operation": "lowercase-hex-encode",
            "rounding": "exact",
            "encoder_range_check": "before-conversion",
        },
    ),
    _codec(
        "color-bgr-u35",
        "integer",
        5,
        (),
        "A 24-bit RGB integer stored as BGR in five base-128 groups.",
        {
            "kind": "integer",
            "unit": "rgb-0xrrggbb",
            "canonical_form": "json-integer",
            "minimum": 0,
            "maximum": 0xFFFFFF,
        },
        _integer_wire(
            35,
            24,
            "unsigned",
            "zero",
            "ignored",
        ),
        _numeric_conversion(
            "channel-reorder",
            0,
            0xFFFFFF,
            "rgb-to-bgr",
            "bgr-to-rgb",
            "exact",
            "before-conversion",
            channel_bits=8,
            json_channel_order="RGB",
            wire_value_channel_order="BGR",
        ),
    ),
    _codec(
        "cstring-7bit",
        "string",
        None,
        (),
        "Null-terminated seven-bit bytes exposed as lowercase hexadecimal.",
        _hex_json(
            "unbounded",
            byte_minimum=1,
            byte_maximum=0x7F,
        ),
        {
            "kind": "zero-terminated-seven-bit-byte-string",
            "group_bits": 7,
            "group_order": "source-order",
            "group_high_bit": "zero",
            "terminator_hex": "00",
            "terminator_included_in_json": False,
            "group_count": {
                "kind": "payload-bytes-plus-terminator",
                "terminator_groups": 1,
            },
            "encoder_padding": "not-applicable",
            "decoder_padding": "not-applicable",
        },
        {
            "kind": "terminated-bytes",
            "encode_operation": "hex-decode-then-append-zero",
            "decode_operation": (
                "read-to-first-zero-then-lowercase-hex-encode"
            ),
            "rounding": "exact",
            "encoder_range_check": "before-conversion",
        },
    ),
    _codec(
        "packed-bytes8-u35",
        "string",
        10,
        (),
        "Eight bytes packed into two five-group unsigned integers.",
        _hex_json("fixed", value=8),
        {
            "kind": "packed-base128-integers",
            "group_bits": 7,
            "group_order": "most-significant-first",
            "group_high_bit": "zero",
            "carrier_bits": 35,
            "value_bits": 32,
            "representation": "unsigned",
            "encoder_padding": "zero",
            "decoder_padding": "zero-only",
            "word_count": 2,
            "groups_per_word": 5,
            "source_bytes_per_word": 4,
            "source_byte_order": "big-endian",
            "word_order": "source-order",
        },
        {
            "kind": "packed-byte-words",
            "encode_operation": "hex-decode-to-big-endian-u32-words",
            "decode_operation": "u32-words-to-lowercase-hex",
            "rounding": "exact",
            "encoder_range_check": "before-conversion",
        },
    ),
    _codec(
        "power-u14",
        "number",
        2,
        (),
        "A percentage from 0 through 100 scaled over unsigned 14 bits.",
        _number_json("percent", 0.0, 100.0),
        _integer_wire(14, 14, "unsigned"),
        _numeric_conversion(
            "proportional-integer",
            0,
            0x3FFF,
            "round-json-times-wire-full-scale-over-json-full-scale",
            "wire-times-json-full-scale-over-wire-full-scale",
            "nearest-ties-toward-positive-infinity",
            "before-conversion",
            json_full_scale=100,
            wire_full_scale=0x3FFF,
        ),
    ),
    _codec(
        "relative-mm-s14",
        "number",
        2,
        (),
        "Signed 14-bit micrometers in two base-128 groups, exposed as mm.",
        _number_json("mm", -8.192, 8.191),
        _integer_wire(14, 14, "twos-complement"),
        _numeric_conversion(
            "scaled-integer",
            -(1 << 13),
            (1 << 13) - 1,
            "round-json-times-scale",
            "wire-divided-by-scale",
            "nearest-ties-to-even",
            "after-rounding",
            scale=_constant_scale(1000),
        ),
    ),
    _codec(
        "s7",
        "integer",
        1,
        (),
        "A signed 7-bit integer in one base-128 group.",
        _integer_json(-(1 << 6), (1 << 6) - 1),
        _integer_wire(7, 7, "twos-complement"),
        _numeric_conversion(
            "identity-integer",
            -(1 << 6),
            (1 << 6) - 1,
            "identity",
            "identity",
            "exact",
            "before-conversion",
        ),
    ),
    _codec(
        "s35",
        "integer",
        5,
        (),
        "A signed 35-bit integer in five base-128 groups.",
        _integer_json(_S35_MIN, _S35_MAX),
        _integer_wire(35, 35, "twos-complement"),
        _numeric_conversion(
            "identity-integer",
            _S35_MIN,
            _S35_MAX,
            "identity",
            "identity",
            "exact",
            "before-conversion",
        ),
    ),
    _codec(
        "scaled-s32",
        "number",
        5,
        ("scale",),
        "A signed 32-bit integer divided by the declared scale.",
        _number_json("field-defined"),
        _integer_wire(
            35,
            32,
            "twos-complement",
            "zero",
            "zero-or-negative-sign-extension",
        ),
        _numeric_conversion(
            "scaled-integer",
            _S32_MIN,
            _S32_MAX,
            "round-json-times-scale",
            "wire-divided-by-scale",
            "nearest-ties-to-even",
            "after-rounding",
            scale=_parameter_scale(),
        ),
    ),
    _codec(
        "scaled-u35",
        "number",
        5,
        ("scale",),
        "An unsigned 35-bit integer divided by the declared scale.",
        _number_json("field-defined"),
        _integer_wire(35, 35, "unsigned"),
        _numeric_conversion(
            "scaled-integer",
            0,
            _U35_MAX,
            "round-json-times-scale",
            "wire-divided-by-scale",
            "nearest-ties-to-even",
            "after-rounding",
            scale=_parameter_scale(),
        ),
    ),
    _codec(
        "u7",
        "integer",
        1,
        (),
        "An unsigned 7-bit integer in one base-128 group.",
        _integer_json(0, 0x7F),
        _integer_wire(7, 7, "unsigned"),
        _numeric_conversion(
            "identity-integer",
            0,
            0x7F,
            "identity",
            "identity",
            "exact",
            "before-conversion",
        ),
    ),
    _codec(
        "u14",
        "integer",
        2,
        (),
        "An unsigned 14-bit integer in two base-128 groups.",
        _integer_json(0, 0x3FFF),
        _integer_wire(14, 14, "unsigned"),
        _numeric_conversion(
            "identity-integer",
            0,
            0x3FFF,
            "identity",
            "identity",
            "exact",
            "before-conversion",
        ),
    ),
    _codec(
        "u35",
        "integer",
        5,
        (),
        "An unsigned 35-bit integer in five base-128 groups.",
        _integer_json(0, _U35_MAX),
        _integer_wire(35, 35, "unsigned"),
        _numeric_conversion(
            "identity-integer",
            0,
            _U35_MAX,
            "identity",
            "identity",
            "exact",
            "before-conversion",
        ),
    ),
)


_FIELD_CODECS: dict[type[Field], str] = {
    AbsoluteMmField: "absolute-mm-s32",
    ByteField: "u7",
    BytesField: "bytes-7bit",
    ColorField: "color-bgr-u35",
    CStringField: "cstring-7bit",
    PackedBytes8Field: "packed-bytes8-u35",
    PowerField: "power-u14",
    RelativeMmField: "relative-mm-s14",
    S7Field: "s7",
    S35Field: "s35",
    ScaledS32Field: "scaled-s32",
    ScaledU35Field: "scaled-u35",
    U14Field: "u14",
    U35Field: "u35",
}


def _codec_catalog(identifiers: set[str]) -> list[dict[str, Any]]:
    declared = {row["id"] for row in _CODEC_DEFINITIONS}
    missing = identifiers - declared
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Catalog codec definitions are missing: {names}")
    return [
        deepcopy(row)
        for row in sorted(
            _CODEC_DEFINITIONS,
            key=lambda value: value["id"],
        )
    ]


def _field_parameters(field: Field) -> dict[str, int | float]:
    if isinstance(field, BytesField):
        return {"size": field.size}
    if isinstance(field, (ScaledS32Field, ScaledU35Field)):
        return {"scale": field.scale}
    return {}


def _field_row(field: Field) -> dict[str, Any]:
    try:
        codec = _FIELD_CODECS[type(field)]
    except KeyError as error:
        name = type(field).__name__
        raise TypeError(f"Field codec is not cataloged: {name}") from error
    return {
        "name": field.name,
        "codec": codec,
        "parameters": _field_parameters(field),
    }


def _command_row(spec: CommandSpec) -> dict[str, Any]:
    return {
        "opcode": spec.opcode.hex(),
        "name": spec.name,
        "fields": [_field_row(field) for field in spec.fields],
        "shape_evidence": spec.shape_evidence,
        "semantic_evidence": spec.semantic_evidence,
        "shape_sources": sorted(spec.shape_sources),
        "semantic_sources": sorted(spec.semantic_sources),
        "controller_effect": spec.controller_effect,
        "reply_behavior": spec.reply_behavior,
        "reply_commands": list(spec.reply_commands),
        "reply_field_matches": [
            {
                "request_field": request_field,
                "reply_field": reply_field,
            }
            for request_field, reply_field in spec.reply_field_matches
        ],
        "notes": spec.notes,
    }


def _validate_reply_contract(spec: CommandSpec) -> None:
    if not spec.reply_commands and not spec.reply_field_matches:
        return
    reply_registry = REGISTRIES["reply"]
    request_fields = {field.name for field in spec.fields}
    reply_specs = []
    for name in spec.reply_commands:
        reply_spec = reply_registry.name(name)
        if reply_spec is None:
            raise ValueError(
                f"Reply contract for {spec.name} names unknown {name}"
            )
        reply_specs.append(reply_spec)
    for request_field, reply_field in spec.reply_field_matches:
        if request_field not in request_fields:
            raise ValueError(
                f"Reply contract for {spec.name} names unknown request "
                f"field {request_field}"
            )
        missing = [
            reply_spec.name
            for reply_spec in reply_specs
            if reply_field not in {
                field.name for field in reply_spec.fields
            }
        ]
        if missing:
            raise ValueError(
                f"Reply contract for {spec.name} names unknown reply "
                f"field {reply_field} in {', '.join(missing)}"
            )


def build_catalog() -> dict[str, Any]:
    """Return the complete catalog as JSON-compatible values."""
    contexts = []
    command_groups: dict[str, dict[str, Any]] = {}
    if (
        set(REGISTRIES) != set(CATALOG_CONTEXTS)
        or set(REGISTRY_CONTEXT_EVIDENCE) != set(CATALOG_CONTEXTS)
    ):
        raise ValueError("Catalog context metadata does not match registries")
    source_ids = {source["id"] for source in _SOURCE_PROVENANCE}
    if source_ids != set(CATALOG_SOURCES):
        raise ValueError("Catalog source provenance does not match sources")
    for name in CATALOG_CONTEXTS:
        contexts.append(
            {
                "name": name,
                "membership_evidence": REGISTRY_CONTEXT_EVIDENCE[name],
            }
        )
        for spec in REGISTRIES[name]:
            _validate_reply_contract(spec)
            row = _command_row(spec)
            identity = json.dumps(
                row,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            command = command_groups.get(identity)
            if command is None:
                command = {"contexts": [], **row}
                command_groups[identity] = command
            command["contexts"].append(name)
    commands = sorted(
        command_groups.values(),
        key=lambda row: (
            row["opcode"],
            row["name"],
            row["contexts"],
        ),
    )
    codecs = {
        field["codec"]
        for command in commands
        for field in command["fields"]
    }
    return {
        "schema": CATALOG_SCHEMA,
        "program_schema": PROGRAM_SCHEMA,
        "catalog_sources": sorted(CATALOG_SOURCES),
        "sources": sorted(
            deepcopy(_SOURCE_PROVENANCE),
            key=lambda source: source["id"],
        ),
        "codecs": _codec_catalog(codecs),
        "contexts": contexts,
        "commands": commands,
    }


def catalog_json() -> str:
    """Serialize the catalog in its canonical deterministic form."""
    return json.dumps(
        build_catalog(),
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the versioned Ruida command catalog.",
    )
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--output", type=Path, help="write the catalog")
    outputs.add_argument(
        "--check",
        type=Path,
        help="fail unless this file is the generated catalog",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing --output file",
    )
    args = parser.parse_args()
    content = catalog_json()
    try:
        if args.check is not None:
            if args.check.read_text(encoding="utf-8") != content:
                print(
                    f"error: catalog is out of date: {args.check}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
        elif args.output is not None:
            atomic_write_text(args.output, content, force=args.force)
        else:
            sys.stdout.write(content)
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
