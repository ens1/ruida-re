"""Generate versioned, language-neutral conformance vectors."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from .catalog import build_catalog, catalog_json
from .cli_io import atomic_write_text
from .codec import swizzle
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
from .registry import (
    SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
    SRC_LIGHTBURN,
    SRC_MEERK40T,
    SRC_RUIDA_PA,
    get_registry,
)
from .transport import checksum, encode_datagram


CONFORMANCE_SCHEMA = "ruida-re.conformance.v1"
EVIDENCE_CLASSIFICATIONS = frozenset(
    (
        "codec-contract",
        "controlled-fixture",
        "hardware-observed",
        "pinned-reference",
        "pinned-reference-agreement",
    )
)

_BASELINE_PATH = (
    "fixtures/lightburn-2.1.03/vector/v001-single-line.rd"
)
_BASELINE_SHA256 = (
    "32117307511f5017b70e3039099d665b"
    "8758c7d24850284de5f0039e826b3819"
)
_POWER_50_PATH = (
    "fixtures/lightburn-2.1.03/matrix/m003-power-050.rd"
)
_POWER_50_SHA256 = (
    "8252279673ec3997537ef113e8ff8569"
    "c76414298be99623170be29f5bdab723"
)
_RELATIVE_PATH = (
    "fixtures/lightburn-2.1.03/advanced/"
    "a002-relative-polyline.rd"
)
_RELATIVE_SHA256 = (
    "d8fa69f966d5b748a048dd9156ab7d67"
    "4201ed13c87cf1022eb78dcee383def8"
)
_HARDWARE_CAPTURE_PATH = (
    "fixtures/hardware/ruida-644xs-usb-serial-v1/manifest-v1.json"
)
_HARDWARE_CAPTURE_SHA256 = (
    "9a9196e6e3cec15548b80890a5ab5980"
    "829e4b00356bf8a9af174f53848b958c"
)
_BASELINE_WITHOUT_CHECKSUM_HEX = (
    "d810e601f0f10200d800e70600000000000000000000e73800e7030000011c20"
    "0000011c20e7070000016a300000011c20e7500000011c200000011c20e75100"
    "00016a300000011c20e7040001000100000000000000000000e70500c9040000"
    "00004e10c631000c66c63200194dc641000c66c64200194dca06000000000000"
    "ca410000e752000000011c200000011c20e753000000016a300000011c20e761"
    "000000011c200000011c20e762000000016a300000011c20ca2200e754000000"
    "000000e754010000000000e755000000000000e755010000000000f103000000"
    "00000000000000f10000f10100f20000f20100f202052a391c41046a150820f2"
    "030000011c200000011c20f2040000016a300000011c20f20500010001000000"
    "4e100000000000f20600000000000000000000f20700ea00e76000e713000001"
    "1c200000011c20e7170000016a300000011c20e7230000011c200000011c20e7"
    "2400e7370000016a300000011c20e708000100010000004e100000000000ca01"
    "00ca0200ca0130ca0110ca0112c9020000004e10c6120000000000c613000000"
    "0000c6500001c6510001c6010c66c602194dc6210c66c622194dca0301880000"
    "011c200000011c20a80000016a300000011c20ebe700da010620000000000a00"
    "0000000ad7"
)


def _evidence(
    classification: str,
    source_ids: tuple[str, ...] = (),
    fixture_path: str | None = None,
    fixture_sha256: str | None = None,
) -> dict[str, Any]:
    if classification not in EVIDENCE_CLASSIFICATIONS:
        raise ValueError(
            f"Unknown conformance evidence classification: "
            f"{classification!r}"
        )
    result: dict[str, Any] = {
        "classification": classification,
        "source_ids": sorted(source_ids),
    }
    if fixture_path is not None and fixture_sha256 is not None:
        result["fixture"] = {
            "path": fixture_path,
            "sha256": fixture_sha256,
        }
    return result


_CODEC_EVIDENCE = _evidence("codec-contract")
_BASELINE_EVIDENCE = _evidence(
    "controlled-fixture",
    (SRC_LIGHTBURN,),
    _BASELINE_PATH,
    _BASELINE_SHA256,
)
_POWER_50_EVIDENCE = _evidence(
    "controlled-fixture",
    (SRC_LIGHTBURN,),
    _POWER_50_PATH,
    _POWER_50_SHA256,
)
_RELATIVE_EVIDENCE = _evidence(
    "controlled-fixture",
    (SRC_LIGHTBURN,),
    _RELATIVE_PATH,
    _RELATIVE_SHA256,
)
_HARDWARE_CAPTURE_EVIDENCE = _evidence(
    "hardware-observed",
    (SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,),
    _HARDWARE_CAPTURE_PATH,
    _HARDWARE_CAPTURE_SHA256,
)


@dataclass(frozen=True)
class _FieldCase:
    identifier: str
    codec: str
    field: Field
    encode_json_value: Any
    evidence: dict[str, Any]


_FIELD_CASES = (
    _FieldCase(
        "field.absolute-mm-s32.fixture-20mm",
        "absolute-mm-s32",
        AbsoluteMmField("value"),
        20.0,
        _BASELINE_EVIDENCE,
    ),
    _FieldCase(
        "field.absolute-mm-s32.negative-1mm",
        "absolute-mm-s32",
        AbsoluteMmField("value"),
        -1.0,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.bytes-7bit.boundaries",
        "bytes-7bit",
        BytesField("value", 3),
        "00017f",
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.color-bgr-u35.channel-order",
        "color-bgr-u35",
        ColorField("value"),
        0x123456,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.cstring-7bit.ruida",
        "cstring-7bit",
        CStringField("value"),
        "5275696461",
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.packed-bytes8-u35.fixture-element-name",
        "packed-bytes8-u35",
        PackedBytes8Field("value"),
        "554e4e414d454420",
        _BASELINE_EVIDENCE,
    ),
    _FieldCase(
        "field.power-u14.fixture-50-percent",
        "power-u14",
        PowerField("value"),
        50.0,
        _POWER_50_EVIDENCE,
    ),
    _FieldCase(
        "field.relative-mm-s14.fixture-negative-3mm",
        "relative-mm-s14",
        RelativeMmField("value"),
        -3.0,
        _RELATIVE_EVIDENCE,
    ),
    _FieldCase(
        "field.s7.negative-one",
        "s7",
        S7Field("value"),
        -1,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.s35.negative-one",
        "s35",
        S35Field("value"),
        -1,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.scaled-s32.negative",
        "scaled-s32",
        ScaledS32Field("value", 100.0),
        -12.34,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.scaled-u35.fixture-speed",
        "scaled-u35",
        ScaledU35Field("value", 1000.0),
        10.0,
        _BASELINE_EVIDENCE,
    ),
    _FieldCase(
        "field.u7.maximum",
        "u7",
        ByteField("value"),
        0x7F,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.u14.maximum",
        "u14",
        U14Field("value"),
        0x3FFF,
        _CODEC_EVIDENCE,
    ),
    _FieldCase(
        "field.u35.maximum",
        "u35",
        U35Field("value"),
        (1 << 35) - 1,
        _CODEC_EVIDENCE,
    ),
)


def _field_parameters(field: Field) -> dict[str, int | float]:
    if isinstance(field, BytesField):
        return {"size": field.size}
    if isinstance(field, (ScaledS32Field, ScaledU35Field)):
        return {"scale": field.scale}
    return {}


def _field_vectors() -> list[dict[str, Any]]:
    vectors = []
    for case in _FIELD_CASES:
        wire = case.field.encode(case.encode_json_value)
        decoded, end = case.field.decode(wire, 0)
        if end != len(wire) or case.field.encode(decoded) != wire:
            raise ValueError(
                f"Field conformance case is not canonical: "
                f"{case.identifier}"
            )
        vectors.append(
            {
                "id": case.identifier,
                "codec": case.codec,
                "parameters": _field_parameters(case.field),
                "encode_json_value": case.encode_json_value,
                "wire_hex": wire.hex(),
                "decode_json_value": decoded,
                "canonical": True,
                "assertions": ["encode", "decode"],
                "evidence": deepcopy(case.evidence),
            }
        )
    return sorted(vectors, key=lambda vector: vector["id"])


def _swizzle_vectors() -> list[dict[str, Any]]:
    logical = bytes(range(256))
    return [
        {
            "id": "swizzle.default-magic.all-byte-values",
            "magic": 0x88,
            "logical_hex": logical.hex(),
            "scrambled_hex": swizzle(logical).hex(),
            "assertions": ["swizzle", "unswizzle"],
            "evidence": _evidence(
                "pinned-reference-agreement",
                (SRC_MEERK40T, SRC_RUIDA_PA),
            ),
        }
    ]


def _job_checksum_vectors() -> list[dict[str, Any]]:
    logical = bytes.fromhex(_BASELINE_WITHOUT_CHECKSUM_HEX)
    value = sum(logical)
    spec = get_registry("job").name("file_checksum")
    if spec is None:
        raise ValueError("The job registry has no file checksum command")
    command = spec.encode({"value": value})
    return [
        {
            "id": "job-checksum.lightburn-single-line",
            "algorithm": "sum-logical-bytes-without-e505-frame",
            "logical_without_checksum_hex": logical.hex(),
            "logical_without_checksum_sha256": sha256(
                logical
            ).hexdigest(),
            "checksum_integer": value,
            "encoded_checksum_command_hex": command.hex(),
            "assertions": ["calculate-checksum", "encode-command"],
            "evidence": deepcopy(_BASELINE_EVIDENCE),
        }
    ]


def _udp_vector(
    identifier: str,
    context: str,
    direction: str,
    logical: bytes,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    scrambled = swizzle(logical)
    datagram = encode_datagram(logical, context)
    if context == "reply":
        framing = "checksumless-scrambled-payload"
        checksum_hex = None
    else:
        framing = "big-endian-u16-checksum-prefix"
        checksum_hex = checksum(scrambled).to_bytes(2, "big").hex()
    return {
        "id": identifier,
        "context": context,
        "direction": direction,
        "framing": framing,
        "magic": 0x88,
        "logical_hex": logical.hex(),
        "scrambled_hex": scrambled.hex(),
        "checksum_hex": checksum_hex,
        "datagram_hex": datagram.hex(),
        "assertions": ["encode-datagram", "decode-datagram"],
        "evidence": evidence,
    }


def _udp_vectors() -> list[dict[str, Any]]:
    return [
        _udp_vector(
            "udp.job.end-of-file",
            "job",
            "host-to-controller",
            bytes.fromhex("d7"),
            _evidence(
                "pinned-reference-agreement",
                (SRC_MEERK40T, SRC_RUIDA_PA),
            ),
        ),
        _udp_vector(
            "udp.reply.acknowledge-checksumless",
            "reply",
            "controller-to-host",
            bytes.fromhex("cc"),
            _evidence(
                "pinned-reference",
                (SRC_RUIDA_PA,),
            ),
        ),
        _udp_vector(
            "udp.request.get-setting-address-1",
            "request",
            "host-to-controller",
            bytes.fromhex("da000001"),
            _evidence(
                "pinned-reference-agreement",
                (SRC_MEERK40T, SRC_RUIDA_PA),
            ),
        ),
    ]


def _serial_message(
    context: str,
    direction: str,
    logical_hex: str,
    wire_hex: str,
    wire_origin: str,
) -> dict[str, Any]:
    logical = bytes.fromhex(logical_hex)
    wire = bytes.fromhex(wire_hex)
    if swizzle(logical) != wire:
        raise ValueError(
            f"Serial {context} message is not canonical"
        )
    return {
        "context": context,
        "direction": direction,
        "logical_hex": logical.hex(),
        "wire_hex": wire.hex(),
        "wire_origin": wire_origin,
    }


def _serial_vectors() -> list[dict[str, Any]]:
    return [
        {
            "id": "serial.exchange.get-setting-address-5",
            "framing": "checksumless-scrambled-stream",
            "magic": 0x88,
            "request": _serial_message(
                "request",
                "host-to-controller",
                "da000005",
                "d489890d",
                "derived-from-logical",
            ),
            "reply": _serial_message(
                "reply",
                "controller-to-host",
                "da0100050000122760",
                "d409890d89899b2fe9",
                "hardware-observed",
            ),
            "separate_acknowledgement": False,
            "assertions": [
                "encode-request-stream",
                "decode-request-stream",
                "decode-reply-stream",
                "correlate-reply-address",
                "no-separate-acknowledgement",
            ],
            "evidence": deepcopy(_HARDWARE_CAPTURE_EVIDENCE),
        }
    ]


def build_conformance() -> dict[str, Any]:
    """Return all version-one conformance vectors."""
    catalog = build_catalog()
    fields = _field_vectors()
    published = {codec["id"] for codec in catalog["codecs"]}
    covered = {vector["codec"] for vector in fields}
    if covered != published:
        missing = ", ".join(sorted(published - covered))
        extra = ", ".join(sorted(covered - published))
        raise ValueError(
            f"Field vector coverage differs: missing={missing}; extra={extra}"
        )
    return {
        "schema": CONFORMANCE_SCHEMA,
        "catalog": {
            "schema": catalog["schema"],
            "sha256": sha256(catalog_json().encode("utf-8")).hexdigest(),
        },
        "sources": deepcopy(catalog["sources"]),
        "field_vectors": fields,
        "swizzle_vectors": _swizzle_vectors(),
        "job_checksum_vectors": _job_checksum_vectors(),
        "serial_vectors": _serial_vectors(),
        "udp_vectors": _udp_vectors(),
    }


def conformance_json() -> str:
    """Serialize the vectors in canonical deterministic form."""
    return json.dumps(
        build_conformance(),
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate versioned Ruida conformance vectors.",
    )
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--output", type=Path, help="write the vectors")
    outputs.add_argument(
        "--check",
        type=Path,
        help="fail unless this file contains the generated vectors",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing --output file",
    )
    args = parser.parse_args()
    content = conformance_json()
    try:
        if args.check is not None:
            if args.check.read_text(encoding="utf-8") != content:
                print(
                    f"error: conformance vectors are out of date: "
                    f"{args.check}",
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


__all__ = (
    "CONFORMANCE_SCHEMA",
    "EVIDENCE_CLASSIFICATIONS",
    "build_conformance",
    "conformance_json",
)


if __name__ == "__main__":
    main()
