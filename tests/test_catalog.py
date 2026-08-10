"""Tests for the language-neutral integration catalog and schemas."""

from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ruida_re.catalog import (
    CATALOG_SCHEMA,
    build_catalog,
    catalog_json,
    main,
)
from ruida_re.fields import (
    AbsoluteMmField,
    ByteField,
    BytesField,
    ColorField,
    CStringField,
    FieldError,
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
from ruida_re.program import (
    SCHEMA as PROGRAM_SCHEMA,
    KnownCommand,
    Program,
    RawSpan,
)
from ruida_re.registry import CATALOG_SOURCES, REGISTRIES


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "spec/catalog-v1.json"
CATALOG_SCHEMA_PATH = ROOT / "schemas/catalog-v1.schema.json"
PROGRAM_SCHEMA_PATH = ROOT / "schemas/program-v1.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CatalogTest(unittest.TestCase):
    def test_checked_in_catalog_is_synchronized(self) -> None:
        self.assertEqual(
            CATALOG_PATH.read_text(encoding="utf-8"),
            catalog_json(),
        )
        self.assertEqual(load_json(CATALOG_PATH), build_catalog())

    def test_catalog_covers_registries_and_declared_codecs(self) -> None:
        catalog = build_catalog()
        self.assertEqual(catalog["schema"], CATALOG_SCHEMA)
        self.assertEqual(catalog["program_schema"], PROGRAM_SCHEMA)
        self.assertEqual(
            catalog["catalog_sources"],
            sorted(CATALOG_SOURCES),
        )
        codecs = {row["id"]: row for row in catalog["codecs"]}
        self.assertEqual(len(codecs), len(catalog["codecs"]))
        schema_codecs = set(
            load_json(CATALOG_SCHEMA_PATH)["$defs"]["codec"]
            ["properties"]["id"]["enum"]
        )
        self.assertEqual(set(codecs), schema_codecs)
        contexts = {row["name"]: row for row in catalog["contexts"]}
        self.assertEqual(set(contexts), set(REGISTRIES))
        for name, registry in REGISTRIES.items():
            with self.subTest(context=name):
                expected = sorted(
                    (spec.opcode.hex(), spec.name) for spec in registry
                )
                actual = [
                    (command["opcode"], command["name"])
                    for command in catalog["commands"]
                    if name in command["contexts"]
                ]
                self.assertEqual(actual, expected)
        used_codecs = set()
        for command in catalog["commands"]:
            self.assertIn(
                command["controller_effect"],
                {
                    "unknown",
                    "read-only",
                    "state-changing",
                    "machine-action",
                },
            )
            self.assertIn(
                command["reply_behavior"],
                {"unknown", "none", "control", "data"},
            )
            self.assertIsInstance(command["reply_commands"], list)
            self.assertIsInstance(command["reply_field_matches"], list)
            for field in command["fields"]:
                codec = codecs[field["codec"]]
                used_codecs.add(field["codec"])
                self.assertEqual(
                    set(field["parameters"]),
                    set(codec["parameters"]),
                )
        self.assertLessEqual(used_codecs, set(codecs))
        get_setting = next(
            command
            for command in catalog["commands"]
            if command["name"] == "get_setting"
            and "request" in command["contexts"]
        )
        self.assertEqual(get_setting["controller_effect"], "read-only")
        self.assertEqual(get_setting["reply_behavior"], "data")
        self.assertEqual(
            get_setting["reply_commands"],
            ["setting_reply"],
        )
        self.assertEqual(
            get_setting["reply_field_matches"],
            [
                {
                    "request_field": "address",
                    "reply_field": "address",
                }
            ],
        )
        self.assertEqual(get_setting["shape_evidence"], "reported")
        self.assertEqual(get_setting["semantic_evidence"], "reported")

    def test_codec_semantics_are_complete_and_consistent(self) -> None:
        codecs = {
            row["id"]: row for row in build_catalog()["codecs"]
        }
        json_kinds = {
            "integer": "integer",
            "finite-number": "number",
            "lowercase-hex-bytes": "string",
        }
        for identifier, codec in codecs.items():
            with self.subTest(codec=identifier):
                semantics = codec["semantics"]
                json_value = semantics["json"]
                wire = semantics["wire"]
                conversion = semantics["conversion"]
                self.assertEqual(
                    codec["json_type"],
                    json_kinds[json_value["kind"]],
                )
                self.assertEqual(wire["group_bits"], 7)
                self.assertEqual(wire["group_high_bit"], "zero")
                self.assertIn("encode_operation", conversion)
                self.assertIn("decode_operation", conversion)
                self.assertIn("rounding", conversion)
                if codec["wire_groups"] is not None:
                    if wire["kind"] == "packed-base128-integers":
                        groups = (
                            wire["word_count"]
                            * wire["groups_per_word"]
                        )
                    else:
                        groups = wire["carrier_bits"] // 7
                    self.assertEqual(groups, codec["wire_groups"])
                parameters = codec["parameters"]
                scale = conversion.get("scale")
                if "scale" in parameters:
                    self.assertEqual(
                        scale,
                        {
                            "kind": "field-parameter",
                            "parameter": "scale",
                            "meaning": "wire-units-per-json-unit",
                        },
                    )
                length = json_value.get("byte_length", {})
                if "size" in parameters:
                    self.assertEqual(
                        length,
                        {
                            "kind": "field-parameter",
                            "parameter": "size",
                        },
                    )

        absolute = codecs["absolute-mm-s32"]["semantics"]
        self.assertEqual(
            absolute["wire"]["decoder_padding"],
            "zero-or-negative-sign-extension",
        )
        self.assertEqual(absolute["wire"]["encoder_padding"], "zero")
        self.assertEqual(
            absolute["conversion"]["rounding"],
            "nearest-ties-to-even",
        )
        self.assertEqual(
            absolute["conversion"]["scale"]["value"],
            1000,
        )

        power = codecs["power-u14"]["semantics"]["conversion"]
        self.assertEqual(power["wire_full_scale"], 0x3FFF)
        self.assertEqual(power["json_full_scale"], 100)
        self.assertEqual(
            power["rounding"],
            "nearest-ties-toward-positive-infinity",
        )

        color = codecs["color-bgr-u35"]["semantics"]
        self.assertEqual(color["conversion"]["json_channel_order"], "RGB")
        self.assertEqual(
            color["conversion"]["wire_value_channel_order"],
            "BGR",
        )
        self.assertEqual(color["wire"]["decoder_padding"], "ignored")
        self.assertEqual(color["wire"]["encoder_padding"], "zero")

        packed = codecs["packed-bytes8-u35"]["semantics"]
        self.assertEqual(packed["json"]["byte_length"]["value"], 8)
        self.assertEqual(packed["wire"]["word_count"], 2)
        self.assertEqual(packed["wire"]["source_bytes_per_word"], 4)
        self.assertEqual(packed["wire"]["source_byte_order"], "big-endian")

        cstring = codecs["cstring-7bit"]["semantics"]
        self.assertEqual(cstring["wire"]["terminator_hex"], "00")
        self.assertFalse(cstring["wire"]["terminator_included_in_json"])
        self.assertEqual(cstring["json"]["byte_minimum"], 1)

    def test_catalog_semantics_reproduce_every_python_field_codec(
        self,
    ) -> None:
        codecs = {
            row["id"]: row for row in build_catalog()["codecs"]
        }
        cases = {
            "absolute-mm-s32": (
                AbsoluteMmField("value"),
                (-1.0, 0.0, 1.2345),
            ),
            "bytes-7bit": (BytesField("value", 2), ("007f",)),
            "color-bgr-u35": (ColorField("value"), (0x123456,)),
            "cstring-7bit": (CStringField("value"), ("", "01417f")),
            "packed-bytes8-u35": (
                PackedBytes8Field("value"),
                ("0080ff123456789a",),
            ),
            "power-u14": (PowerField("value"), (0.0, 50.0, 100.0)),
            "relative-mm-s14": (
                RelativeMmField("value"),
                (-1.234, 0.0, 8.191),
            ),
            "s7": (S7Field("value"), (-64, -1, 0, 63)),
            "s35": (S35Field("value"), (-(1 << 34), -1, 1 << 20)),
            "scaled-s32": (
                ScaledS32Field("value", 100.0),
                (-12.345, 0.0, 12.345),
            ),
            "scaled-u35": (
                ScaledU35Field("value", 100.0),
                (0.0, 12.345),
            ),
            "u7": (ByteField("value"), (0, 127)),
            "u14": (U14Field("value"), (0, 0x3FFF)),
            "u35": (U35Field("value"), (0, (1 << 35) - 1)),
        }
        self.assertEqual(set(cases), set(codecs))
        for identifier, (field, values) in cases.items():
            codec = codecs[identifier]
            parameters = self.field_parameters(field)
            for value in values:
                with self.subTest(codec=identifier, value=value):
                    expected = field.encode(value)
                    encoded = self.catalog_encode(
                        codec,
                        value,
                        parameters,
                    )
                    self.assertEqual(encoded, expected)
                    decoded, offset = field.decode(expected, 0)
                    self.assertEqual(offset, len(expected))
                    reference = self.catalog_decode(
                        codec,
                        expected,
                        parameters,
                    )
                    if isinstance(decoded, float):
                        self.assertAlmostEqual(reference, decoded, places=12)
                    else:
                        self.assertEqual(reference, decoded)

    def test_source_provenance_resolves_every_reference(self) -> None:
        catalog = build_catalog()
        sources = {source["id"]: source for source in catalog["sources"]}
        self.assertEqual(set(sources), set(catalog["catalog_sources"]))
        referenced = set()
        for command in catalog["commands"]:
            referenced.update(command["shape_sources"])
            referenced.update(command["semantic_sources"])
        self.assertLessEqual(referenced, set(sources))
        for identifier, source in sources.items():
            with self.subTest(source=identifier):
                if identifier.startswith("github:"):
                    revision = identifier.rsplit("@", 1)[1]
                    self.assertEqual(source["revision"], revision)
                    self.assertTrue(source["url"].endswith(revision))
                    self.assertIsNotNone(source["license"])
                else:
                    self.assertEqual(source["kind"], "local-fixture")
                    self.assertIsNone(source["url"])
                    self.assertIsNone(source["license"])
                    self.assertTrue(source["local_path"].startswith(
                        "fixtures/"
                    ))

    def test_declared_padding_variants_match_python_decoders(self) -> None:
        codecs = {
            row["id"]: row for row in build_catalog()["codecs"]
        }
        absolute_codec = codecs["absolute-mm-s32"]
        absolute = AbsoluteMmField("value")
        canonical = absolute.encode(-1.0)
        self.assertEqual(self.base128_integer(canonical) >> 32, 0)
        sign_extended = self.base128(
            (7 << 32) | ((-1000) & 0xFFFFFFFF),
            5,
        )
        self.assertEqual(absolute.decode(sign_extended, 0)[0], -1.0)
        invalid_padding = self.base128((1 << 33) - 1, 5)
        with self.assertRaises(FieldError):
            absolute.decode(invalid_padding, 0)
        self.assertEqual(
            absolute_codec["semantics"]["wire"]["decoder_padding"],
            "zero-or-negative-sign-extension",
        )

        color_codec = codecs["color-bgr-u35"]
        color = ColorField("value")
        canonical_color = color.encode(0x123456)
        padded_color = self.base128(
            self.base128_integer(canonical_color) | (1 << 34),
            5,
        )
        self.assertEqual(color.decode(padded_color, 0)[0], 0x123456)
        self.assertEqual(
            color_codec["semantics"]["wire"]["decoder_padding"],
            "ignored",
        )

        packed_codec = codecs["packed-bytes8-u35"]
        packed = PackedBytes8Field("value")
        invalid_word = self.base128(1 << 32, 5) + self.base128(0, 5)
        with self.assertRaises(FieldError):
            packed.decode(invalid_word, 0)
        self.assertEqual(
            packed_codec["semantics"]["wire"]["decoder_padding"],
            "zero-only",
        )

    def test_schema_required_fields_are_coherent(self) -> None:
        catalog_schema = load_json(CATALOG_SCHEMA_PATH)
        program_schema = load_json(PROGRAM_SCHEMA_PATH)
        for schema in (catalog_schema, program_schema):
            self.assertEqual(
                schema["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )
            self.assert_required_declared(schema)
            self.assert_local_references_resolve(schema, schema)
        self.assertEqual(
            catalog_schema["properties"]["schema"]["const"],
            CATALOG_SCHEMA,
        )
        self.assertEqual(
            program_schema["properties"]["schema"]["const"],
            PROGRAM_SCHEMA,
        )
        catalog_definitions = catalog_schema["$defs"]
        self.assertEqual(
            set(catalog_definitions["codec"]["properties"]["id"]["enum"]),
            set(catalog_definitions["field"]["properties"]["codec"]["enum"]),
        )
        self.assertEqual(
            set(
                catalog_definitions["codec"]["properties"]
                ["parameters"]["items"]["enum"]
            ),
            set(
                catalog_definitions["field"]["properties"]
                ["parameters"]["properties"]
            ),
        )
        self.assert_catalog_required_fields(catalog_schema)
        self.assert_program_required_fields(program_schema)

    def test_catalog_output_is_no_clobber_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            with patch.object(
                sys,
                "argv",
                ["ruida-catalog", "--output", str(path)],
            ):
                main()
            original = path.read_text(encoding="utf-8")
            with patch.object(
                sys,
                "argv",
                ["ruida-catalog", "--output", str(path)],
            ):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main()
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            with patch.object(
                sys,
                "argv",
                [
                    "ruida-catalog",
                    "--output",
                    str(path),
                    "--force",
                ],
            ):
                main()
            self.assertEqual(path.read_text(encoding="utf-8"), catalog_json())

    def assert_required_declared(self, node) -> None:
        if isinstance(node, dict):
            if "required" in node and "properties" in node:
                required = node["required"]
                self.assertEqual(len(required), len(set(required)))
                self.assertLessEqual(
                    set(required),
                    set(node.get("properties", {})),
                )
            for value in node.values():
                self.assert_required_declared(value)
        elif isinstance(node, list):
            for value in node:
                self.assert_required_declared(value)

    def assert_local_references_resolve(self, root, node) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if reference is not None:
                prefix = "#/$defs/"
                self.assertTrue(reference.startswith(prefix))
                self.assertIn(reference.removeprefix(prefix), root["$defs"])
            for value in node.values():
                self.assert_local_references_resolve(root, value)
        elif isinstance(node, list):
            for value in node:
                self.assert_local_references_resolve(root, value)

    def assert_catalog_required_fields(self, schema) -> None:
        catalog = build_catalog()
        self.assert_has_required(schema, catalog)
        definitions = schema["$defs"]
        for codec in catalog["codecs"]:
            self.assert_has_required(definitions["codec"], codec)
            semantics = codec["semantics"]
            self.assert_has_required(
                definitions["codecSemantics"],
                semantics,
            )
            self.assert_has_required(
                definitions["jsonSemantics"],
                semantics["json"],
            )
            self.assert_has_required(
                definitions["wireSemantics"],
                semantics["wire"],
            )
            conversion = semantics["conversion"]
            self.assert_has_required(
                definitions["conversionSemantics"],
                conversion,
            )
            if "byte_length" in semantics["json"]:
                self.assert_has_required(
                    definitions["byteLength"],
                    semantics["json"]["byte_length"],
                )
            if "scale" in conversion:
                self.assert_has_required(
                    definitions["scale"],
                    conversion["scale"],
                )
            if "group_count" in semantics["wire"]:
                self.assert_has_required(
                    definitions["groupCount"],
                    semantics["wire"]["group_count"],
                )
        for source in catalog["sources"]:
            self.assert_has_required(definitions["source"], source)
        for context in catalog["contexts"]:
            self.assert_has_required(definitions["context"], context)
        for command in catalog["commands"]:
            self.assert_has_required(definitions["command"], command)
            for field in command["fields"]:
                self.assert_has_required(definitions["field"], field)

    def assert_program_required_fields(self, schema) -> None:
        self.assert_has_required(schema, Program().to_dict())
        definitions = schema["$defs"]
        command = KnownCommand(
            offset=0,
            opcode="d7",
            name="end_of_file",
            values={},
        )
        self.assert_has_required(
            definitions["commandRecord"],
            command.to_dict(),
        )
        self.assert_has_required(
            definitions["rawRecord"],
            RawSpan(offset=0, raw="00").to_dict(),
        )

    def assert_has_required(self, schema, value) -> None:
        self.assertLessEqual(set(schema["required"]), set(value))

    def field_parameters(self, field) -> dict[str, int | float]:
        if isinstance(field, BytesField):
            return {"size": field.size}
        if isinstance(field, (ScaledS32Field, ScaledU35Field)):
            return {"scale": field.scale}
        return {}

    def catalog_encode(self, codec, value, parameters) -> bytes:
        semantics = codec["semantics"]
        conversion = semantics["conversion"]
        kind = conversion["kind"]
        if kind == "identity-bytes":
            return bytes.fromhex(value)
        if kind == "terminated-bytes":
            return bytes.fromhex(value) + b"\x00"
        if kind == "packed-byte-words":
            raw = bytes.fromhex(value)
            words = (
                int.from_bytes(raw[:4], "big"),
                int.from_bytes(raw[4:], "big"),
            )
            return b"".join(self.base128(word, 5) for word in words)
        if kind == "identity-integer":
            integer = value
        elif kind == "scaled-integer":
            scale = self.scale_value(conversion["scale"], parameters)
            integer = round(float(value) * scale)
        elif kind == "proportional-integer":
            numerator = float(value) * conversion["wire_full_scale"]
            scaled = numerator / conversion["json_full_scale"]
            integer = int(scaled + 0.5)
        elif kind == "channel-reorder":
            integer = ((value & 0xFF) << 16) | (value & 0xFF00)
            integer |= (value >> 16) & 0xFF
        else:
            self.fail(f"Unhandled catalog conversion {kind}")
        wire = semantics["wire"]
        if wire["representation"] == "twos-complement":
            integer &= (1 << wire["value_bits"]) - 1
        return self.base128(integer, codec["wire_groups"])

    def catalog_decode(self, codec, data, parameters):
        semantics = codec["semantics"]
        conversion = semantics["conversion"]
        kind = conversion["kind"]
        if kind == "identity-bytes":
            return data.hex()
        if kind == "terminated-bytes":
            return data[: data.index(0)].hex()
        if kind == "packed-byte-words":
            words = (
                self.base128_integer(data[:5]),
                self.base128_integer(data[5:]),
            )
            return b"".join(
                word.to_bytes(4, "big") for word in words
            ).hex()
        wire = semantics["wire"]
        integer = self.base128_integer(data)
        value_bits = wire["value_bits"]
        low = integer & ((1 << value_bits) - 1)
        if (
            wire["representation"] == "twos-complement"
            and low >= 1 << (value_bits - 1)
        ):
            low -= 1 << value_bits
        if kind == "identity-integer":
            return low
        if kind == "scaled-integer":
            scale = self.scale_value(conversion["scale"], parameters)
            return low / scale
        if kind == "proportional-integer":
            return (
                low
                * conversion["json_full_scale"]
                / conversion["wire_full_scale"]
            )
        if kind == "channel-reorder":
            result = ((low & 0xFF) << 16) | (low & 0xFF00)
            return result | ((low >> 16) & 0xFF)
        self.fail(f"Unhandled catalog conversion {kind}")

    def scale_value(self, scale, parameters) -> float:
        if scale["kind"] == "constant":
            return scale["value"]
        return parameters[scale["parameter"]]

    def base128(self, value: int, groups: int) -> bytes:
        return bytes(
            (value >> shift) & 0x7F
            for shift in range((groups - 1) * 7, -1, -7)
        )

    def base128_integer(self, data: bytes) -> int:
        result = 0
        for value in data:
            result = (result << 7) | value
        return result


if __name__ == "__main__":
    unittest.main()
