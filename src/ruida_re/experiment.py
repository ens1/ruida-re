"""Validate and compare controlled Ruida capability experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path, PureWindowsPath
from typing import Any, TypeAlias

from .cli_io import atomic_write_text
from .codec import swizzle
from .diff import Change, compare
from .jsonio import integer as json_integer
from .jsonio import loads as load_json
from .jsonio import number as json_number
from .program import KnownCommand, Program, RawSpan, Record, decode
from .registry import get_registry

SCHEMA = "ruida-re.experiment.v1"
REPORT_SCHEMA = "ruida-re.experiment-report.v1"
RELATIONS = frozenset(("different", "identical", "observe"))
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DIFF_WRAPPER = b"RDWORKV000"

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class ProtocolSpec:
    """Protocol decoding parameters shared by every capture."""

    magic: int
    context: str
    container: str


@dataclass(frozen=True)
class CaptureSpec:
    """One content-addressed machine-file capture."""

    identifier: str
    path: str
    sha256: str
    controls: dict[str, JsonValue]
    provenance: dict[str, JsonValue]


@dataclass(frozen=True)
class ComparisonSpec:
    """One declared single-variable baseline/variant comparison."""

    identifier: str
    baseline: str
    variant: str
    variable: str
    expected_relation: str


@dataclass(frozen=True)
class ExperimentManifest:
    """A validated capability-experiment manifest."""

    capability: str
    provenance: dict[str, JsonValue]
    protocol: ProtocolSpec
    strict: bool
    captures: tuple[CaptureSpec, ...]
    comparisons: tuple[ComparisonSpec, ...]


@dataclass(frozen=True)
class _CaptureAnalysis:
    spec: CaptureSpec
    raw_data: bytes | None
    program: Program | None
    report: dict[str, Any]


def _exact_fields(
    value: object,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    if not all(isinstance(name, str) for name in value):
        raise ValueError(f"{label} field names must be strings")
    optional = optional or set()
    actual = set(value)
    allowed = required | optional
    if not required <= actual or not actual <= allowed:
        raise ValueError(
            f"{label} fields do not match: "
            f"missing={sorted(required - actual)}, "
            f"extra={sorted(actual - allowed)}"
        )
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a stable lowercase identifier")
    return value


def _json_value(value: object, label: str) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float, Decimal)):
        return json_number(value, label)
    if isinstance(value, list):
        return [
            _json_value(item, f"{label}[{index}]") for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for name, item in value.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{label} keys must be non-empty strings")
            result[name] = _json_value(item, f"{label}.{name}")
        return result
    raise ValueError(f"{label} must be a JSON value")


def _json_mapping(value: object, label: str) -> dict[str, JsonValue]:
    normalized = _json_value(value, label)
    if not isinstance(normalized, dict):
        raise TypeError(f"{label} must be an object")
    return normalized


def _protocol(value: object) -> ProtocolSpec:
    item = _exact_fields(
        value,
        "Protocol",
        {"magic", "context", "container"},
    )
    magic = json_integer(
        item["magic"],
        "Protocol magic",
        minimum=0,
        maximum=0xFF,
    )
    context = item["context"]
    if context not in ("job", "request", "reply"):
        raise ValueError(f"Unknown protocol context: {context!r}")
    container = item["container"]
    if container != "rd":
        raise ValueError("Capability captures must use the rd container")
    return ProtocolSpec(magic, context, container)


def _capture(value: object, index: int) -> CaptureSpec:
    label = f"Capture {index}"
    item = _exact_fields(
        value,
        label,
        {"id", "path", "sha256", "controls"},
        {"provenance"},
    )
    identifier = _identifier(item["id"], f"{label} id")
    path = item["path"]
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError(f"{label} path must be a non-empty string")
    capture_path = Path(path)
    if capture_path.is_absolute() or PureWindowsPath(path).drive:
        raise ValueError(f"{label} path must be relative to the manifest")
    if "\\" in path or ".." in capture_path.parts:
        raise ValueError(f"{label} path must not escape the manifest")
    if capture_path.suffix.lower() != ".rd":
        raise ValueError(f"{label} path must name an .rd file")
    digest = item["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError(f"{label} sha256 must be canonical lowercase hex")
    controls = _json_mapping(item["controls"], f"{label} controls")
    if not controls:
        raise ValueError(f"{label} controls must not be empty")
    provenance = _json_mapping(
        item.get("provenance", {}),
        f"{label} provenance",
    )
    return CaptureSpec(identifier, path, digest, controls, provenance)


def _comparison(value: object, index: int) -> ComparisonSpec:
    label = f"Comparison {index}"
    item = _exact_fields(
        value,
        label,
        {
            "id",
            "baseline",
            "variant",
            "variable",
            "expected_relation",
        },
    )
    identifier = _identifier(item["id"], f"{label} id")
    baseline = _identifier(item["baseline"], f"{label} baseline")
    variant = _identifier(item["variant"], f"{label} variant")
    if baseline == variant:
        raise ValueError(f"{label} must reference two distinct captures")
    variable = item["variable"]
    if not isinstance(variable, str) or not variable:
        raise ValueError(f"{label} variable must be a non-empty string")
    relation = item["expected_relation"]
    if not isinstance(relation, str) or relation not in RELATIONS:
        raise ValueError(
            f"{label} expected_relation must be 'different', 'identical', or 'observe'"
        )
    return ComparisonSpec(
        identifier,
        baseline,
        variant,
        variable,
        relation,
    )


def _unique(items: list[str], label: str) -> None:
    duplicate = next(
        (item for index, item in enumerate(items) if item in items[:index]),
        None,
    )
    if duplicate is not None:
        raise ValueError(f"Duplicate {label}: {duplicate!r}")


def _validate_comparison_controls(
    comparison: ComparisonSpec,
    captures: dict[str, CaptureSpec],
) -> None:
    try:
        baseline = captures[comparison.baseline]
        variant = captures[comparison.variant]
    except KeyError as error:
        raise ValueError(
            f"Comparison {comparison.identifier!r} references unknown "
            f"capture {error.args[0]!r}"
        ) from error
    if set(baseline.controls) != set(variant.controls):
        raise ValueError(f"Comparison {comparison.identifier!r} control fields differ")
    if comparison.variable not in baseline.controls:
        raise ValueError(
            f"Comparison {comparison.identifier!r} variable is not a declared control"
        )
    changed = {
        name
        for name in baseline.controls
        if baseline.controls[name] != variant.controls[name]
    }
    if changed != {comparison.variable}:
        raise ValueError(
            f"Comparison {comparison.identifier!r} must change exactly "
            f"{comparison.variable!r}; changed={sorted(changed)!r}"
        )


def parse_manifest(value: object) -> ExperimentManifest:
    """Validate and normalize an experiment manifest document."""
    item = _exact_fields(
        value,
        "Experiment manifest",
        {
            "schema",
            "capability",
            "protocol",
            "captures",
            "comparisons",
        },
        {"provenance", "strict"},
    )
    if item["schema"] != SCHEMA:
        raise ValueError(f"Unsupported schema: {item['schema']!r}")
    capability = _identifier(item["capability"], "Capability")
    provenance = _json_mapping(
        item.get("provenance", {}),
        "Provenance",
    )
    strict = item.get("strict", True)
    if not isinstance(strict, bool):
        raise TypeError("Strict must be a boolean")
    capture_values = item["captures"]
    comparison_values = item["comparisons"]
    if not isinstance(capture_values, list) or len(capture_values) < 2:
        raise ValueError("Captures must contain at least two entries")
    if not isinstance(comparison_values, list) or not comparison_values:
        raise ValueError("Comparisons must contain at least one entry")
    captures = tuple(
        _capture(capture, index) for index, capture in enumerate(capture_values)
    )
    comparisons = tuple(
        _comparison(comparison, index)
        for index, comparison in enumerate(comparison_values)
    )
    _unique([capture.identifier for capture in captures], "capture id")
    _unique([capture.path for capture in captures], "capture path")
    _unique(
        [comparison.identifier for comparison in comparisons],
        "comparison id",
    )
    capture_map = {capture.identifier: capture for capture in captures}
    for comparison in comparisons:
        _validate_comparison_controls(comparison, capture_map)
    return ExperimentManifest(
        capability,
        provenance,
        _protocol(item["protocol"]),
        strict,
        captures,
        comparisons,
    )


def load_manifest(path: Path) -> ExperimentManifest:
    """Read and validate one experiment manifest."""
    value = load_json(path.read_text(encoding="utf-8"))
    return parse_manifest(value)


def _fixture_cases(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise TypeError("Capability fixture cases must be an array")
    return value


def _fixture_case(value: JsonValue, index: int) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise TypeError(f"Capability fixture case {index} must be an object")
    return value


def _captured_fixture_case(
    item: dict[str, JsonValue],
    index: int,
) -> tuple[str, dict[str, JsonValue]]:
    label = f"Capability fixture case {index}"
    identifier = _identifier(item.get("identifier"), f"{label} identifier")
    expected_rd = item.get("expected_rd")
    controls = item.get("controls")
    files = item.get("files")
    if not isinstance(files, dict):
        raise TypeError(f"{label} files must be an object")
    if not isinstance(expected_rd, str):
        raise TypeError(f"{label} expected_rd must be a string")
    metadata = files.get(expected_rd)
    if not isinstance(metadata, dict):
        raise TypeError(f"{label} has no captured file metadata for {expected_rd!r}")
    capture: dict[str, JsonValue] = {
        "id": identifier,
        "path": expected_rd,
        "sha256": metadata.get("sha256"),
        "controls": controls,
        "provenance": _fixture_capture_provenance(item, expected_rd),
    }
    _capture(capture, index)
    return identifier, capture


def _fixture_capture_provenance(
    item: dict[str, JsonValue],
    expected_rd: str,
) -> dict[str, JsonValue]:
    provenance = {name: value for name, value in item.items() if name != "expected_rd"}
    files = provenance.get("files")
    if not isinstance(files, dict):
        raise TypeError("Captured capability fixture files must be an object")
    provenance["files"] = {
        name: metadata for name, metadata in files.items() if name != expected_rd
    }
    rd_metadata = files.get(expected_rd)
    if not isinstance(rd_metadata, dict):
        raise TypeError("Captured RD file metadata must be an object")
    nonredundant_rd_metadata = {
        name: value for name, value in rd_metadata.items() if name != "sha256"
    }
    if nonredundant_rd_metadata:
        provenance["rd_file_metadata"] = nonredundant_rd_metadata
    return provenance


def _fixture_comparison(
    item: dict[str, JsonValue],
    index: int,
    identifier: str,
) -> tuple[str, str] | None:
    value = item.get("comparison")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"Capability fixture case {index} comparison must be an object")
    baseline = _identifier(
        value.get("baseline"),
        f"Capability fixture case {index} comparison baseline",
    )
    variable = value.get("independent_variable")
    if not isinstance(variable, str) or not variable:
        raise ValueError(
            f"Capability fixture case {index} comparison independent_variable "
            "must be a non-empty string"
        )
    if baseline == identifier:
        raise ValueError(f"Capability fixture case {index} cannot compare itself")
    return baseline, variable


def manifest_from_capability_fixture(
    value: object,
    family: str,
    *,
    strict: bool = True,
) -> dict[str, JsonValue]:
    """Build an experiment document from captured cases in one family.

    The input is consumed structurally so the experiment layer does not depend
    on the fixture generator or its Python model. Pending captures and
    comparisons whose baseline is not captured are excluded.
    """
    if not isinstance(strict, bool):
        raise TypeError("Strict must be a boolean")
    family = _identifier(family, "Capability family")
    source = _json_mapping(value, "Capability fixture manifest")
    source_schema = source.get("schema")
    if not isinstance(source_schema, str) or not source_schema:
        raise ValueError(
            "Capability fixture manifest schema must be a non-empty string"
        )
    cases_value = source.get("cases")
    if cases_value is None:
        raise ValueError("Capability fixture manifest is missing cases")

    captured: dict[str, dict[str, JsonValue]] = {}
    comparison_specs: dict[str, tuple[str, str]] = {}
    for index, case_value in enumerate(_fixture_cases(cases_value)):
        item = _fixture_case(case_value, index)
        if item.get("family") != family:
            continue
        if item.get("export_status") != "captured":
            continue
        identifier, capture = _captured_fixture_case(item, index)
        if identifier in captured:
            raise ValueError(f"Duplicate captured case id: {identifier!r}")
        captured[identifier] = capture
        fixture_comparison = _fixture_comparison(item, index, identifier)
        if fixture_comparison is not None:
            comparison_specs[identifier] = fixture_comparison

    comparisons: list[JsonValue] = []
    referenced: set[str] = set()
    for variant, (baseline, variable) in sorted(comparison_specs.items()):
        if baseline not in captured:
            continue
        referenced.update((baseline, variant))
        comparison_document: dict[str, JsonValue] = {
            "id": f"{baseline}-vs-{variant}",
            "baseline": baseline,
            "variant": variant,
            "variable": variable,
            "expected_relation": "observe",
        }
        comparisons.append(comparison_document)
    if not comparisons:
        raise ValueError(f"Capability family {family!r} has no captured comparisons")

    provenance = {name: item for name, item in source.items() if name != "cases"}
    capture_documents: list[JsonValue] = [captured[name] for name in sorted(referenced)]
    document: dict[str, JsonValue] = {
        "schema": SCHEMA,
        "capability": family,
        "provenance": provenance,
        "protocol": {
            "magic": 0x88,
            "context": "job",
            "container": "rd",
        },
        "strict": strict,
        "captures": capture_documents,
        "comparisons": comparisons,
    }
    parse_manifest(document)
    return document


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checksum_report(
    program: Program,
) -> tuple[list[dict[str, Any]], bool | None]:
    checksums = []
    for index, record in enumerate(program.records):
        if not (isinstance(record, KnownCommand) and record.name == "file_checksum"):
            continue
        value = record.values.get("value")
        checksums.append(
            {
                "index": index,
                "offset": record.offset,
                "value": value,
                "matches_source_basis": (value == program.source_checksum_basis),
            }
        )
    if not checksums:
        return checksums, None
    return checksums, all(item["matches_source_basis"] for item in checksums)


def _analyze_capture(
    root: Path,
    spec: CaptureSpec,
    protocol: ProtocolSpec,
    strict: bool,
) -> _CaptureAnalysis:
    report: dict[str, Any] = {
        "id": spec.identifier,
        "path": spec.path,
        "expected_sha256": spec.sha256,
        "controls": spec.controls,
        "provenance": spec.provenance,
    }
    try:
        path = _resolve_capture_path(root, spec, strict=True)
        raw_data = path.read_bytes()
    except (OSError, ValueError) as error:
        report.update(
            {
                "valid": False,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return _CaptureAnalysis(spec, None, None, report)
    digest = _sha256(raw_data)
    try:
        program = decode(
            raw_data,
            magic=protocol.magic,
            context=protocol.context,
            container=protocol.container,
        )
        encoded = program.encode()
    except (KeyError, TypeError, ValueError) as error:
        report.update(
            {
                "size": len(raw_data),
                "sha256": digest,
                "sha256_matches": digest == spec.sha256,
                "valid": False,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return _CaptureAnalysis(spec, raw_data, None, report)
    opaque = sum(isinstance(record, RawSpan) for record in program.records)
    checksums, checksum_consistent = _checksum_report(program)
    checksum_required = protocol.context == "job" and protocol.container == "rd"
    checksum_valid = (
        checksum_consistent is True
        if checksum_required
        else checksum_consistent is not False
    )
    structured = not program.issues and opaque == 0
    exact = encoded == raw_data
    digest_matches = digest == spec.sha256
    report.update(
        {
            "size": len(raw_data),
            "sha256": digest,
            "sha256_matches": digest_matches,
            "records": len(program.records),
            "known_records": len(program.records) - opaque,
            "opaque_records": opaque,
            "issues": program.issues,
            "structured": structured,
            "round_trip_exact": exact,
            "checksum_records": checksums,
            "checksum_consistent": checksum_consistent,
            "checksum_required": checksum_required,
            "valid": (
                digest_matches
                and exact
                and checksum_valid
                and (structured or not strict)
            ),
        }
    )
    return _CaptureAnalysis(spec, raw_data, program, report)


def _resolve_capture_path(
    root: Path,
    spec: CaptureSpec,
    *,
    strict: bool,
) -> Path:
    path = (root / spec.path).resolve(strict=strict)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"Capture path escapes the manifest directory: {spec.path}"
        ) from error
    return path


def _is_checksum(record: Record) -> bool:
    return isinstance(record, KnownCommand) and record.name == "file_checksum"


def _record_parts(
    program: Program,
    checksum: bool,
) -> list[tuple[int, Record, bytes]]:
    registry = get_registry(program.context)
    return [
        (index, record, record.encode(registry))
        for index, record in enumerate(program.records)
        if _is_checksum(record) is checksum
    ]


def _record_view(
    item: tuple[int, Record, bytes],
) -> dict[str, Any]:
    index, record, encoded = item
    result = record.to_dict()
    result["index"] = index
    result["encoded"] = encoded.hex()
    return result


def _record_diff(
    before: list[tuple[int, Record, bytes]],
    after: list[tuple[int, Record, bytes]],
) -> list[dict[str, Any]]:
    matcher = SequenceMatcher(
        None,
        [item[2] for item in before],
        [item[2] for item in after],
        autojunk=False,
    )
    changes = []
    for (
        operation,
        before_start,
        before_end,
        after_start,
        after_end,
    ) in matcher.get_opcodes():
        if operation == "equal":
            continue
        changes.append(
            {
                "operation": operation,
                "before_start": before_start,
                "before_end": before_end,
                "after_start": after_start,
                "after_end": after_end,
                "before_records": [
                    _record_view(item) for item in before[before_start:before_end]
                ],
                "after_records": [
                    _record_view(item) for item in after[after_start:after_end]
                ],
            }
        )
    return changes


def _change_view(change: Change) -> dict[str, Any]:
    return {
        "operation": change.operation,
        "before_start": change.before_start,
        "before_end": change.before_end,
        "after_start": change.after_start,
        "after_end": change.after_end,
        "before": change.before.hex(),
        "after": change.after.hex(),
    }


def _unswizzled_diff(
    before: list[tuple[int, Record, bytes]],
    after: list[tuple[int, Record, bytes]],
    magic: int,
) -> list[dict[str, Any]]:
    before_stream = _DIFF_WRAPPER + swizzle(
        b"".join(item[2] for item in before),
        magic,
    )
    after_stream = _DIFF_WRAPPER + swizzle(
        b"".join(item[2] for item in after),
        magic,
    )
    return [
        _change_view(change) for change in compare(before_stream, after_stream, magic)
    ]


def _unavailable_comparison(
    spec: ComparisonSpec,
    unavailable: list[str],
) -> dict[str, Any]:
    return {
        "id": spec.identifier,
        "baseline": spec.baseline,
        "variant": spec.variant,
        "variable": spec.variable,
        "expected_relation": spec.expected_relation,
        "valid": False,
        "error": f"Capture analysis unavailable: {', '.join(unavailable)}",
    }


def _analyze_comparison(
    spec: ComparisonSpec,
    captures: dict[str, _CaptureAnalysis],
    magic: int,
) -> dict[str, Any]:
    baseline = captures[spec.baseline]
    variant = captures[spec.variant]
    unavailable = [
        item.spec.identifier
        for item in (baseline, variant)
        if item.program is None or item.raw_data is None
    ]
    if unavailable:
        return _unavailable_comparison(spec, unavailable)
    assert baseline.program is not None
    assert baseline.raw_data is not None
    assert variant.program is not None
    assert variant.raw_data is not None
    before_normal = _record_parts(baseline.program, False)
    after_normal = _record_parts(variant.program, False)
    before_checksum = _record_parts(baseline.program, True)
    after_checksum = _record_parts(variant.program, True)
    before_logical = b"".join(item[2] for item in before_normal)
    after_logical = b"".join(item[2] for item in after_normal)
    protocol_relation = "identical" if before_logical == after_logical else "different"
    raw_relation = "identical" if baseline.raw_data == variant.raw_data else "different"
    relation_matches = (
        spec.expected_relation == "observe"
        or protocol_relation == spec.expected_relation
    )
    return {
        "id": spec.identifier,
        "baseline": spec.baseline,
        "variant": spec.variant,
        "variable": spec.variable,
        "baseline_value": baseline.spec.controls[spec.variable],
        "variant_value": variant.spec.controls[spec.variable],
        "expected_relation": spec.expected_relation,
        "protocol_relation": protocol_relation,
        "raw_relation": raw_relation,
        "relation_matches": relation_matches,
        "record_diff": {
            "non_checksum": _record_diff(
                before_normal,
                after_normal,
            ),
            "checksum_derived": _record_diff(
                before_checksum,
                after_checksum,
            ),
        },
        "unswizzled_diff": {
            "non_checksum": _unswizzled_diff(
                before_normal,
                after_normal,
                magic,
            ),
            "checksum_derived": _unswizzled_diff(
                before_checksum,
                after_checksum,
                magic,
            ),
        },
        "valid": bool(
            baseline.report["valid"] and variant.report["valid"] and relation_matches
        ),
    }


def analyze(
    manifest_path: Path,
    *,
    strict: bool | None = None,
) -> dict[str, Any]:
    """Analyze all captures and declared pairs in one manifest."""
    if strict is not None and not isinstance(strict, bool):
        raise TypeError("Strict override must be a boolean")
    manifest_data = manifest_path.read_bytes()
    manifest = parse_manifest(load_json(manifest_data.decode("utf-8")))
    effective_strict = manifest.strict if strict is None else strict
    root = manifest_path.resolve().parent
    captures = [
        _analyze_capture(
            root,
            spec,
            manifest.protocol,
            effective_strict,
        )
        for spec in manifest.captures
    ]
    capture_map = {capture.spec.identifier: capture for capture in captures}
    comparisons = [
        _analyze_comparison(
            comparison,
            capture_map,
            manifest.protocol.magic,
        )
        for comparison in manifest.comparisons
    ]
    capture_reports = [capture.report for capture in captures]
    valid = all(item["valid"] for item in capture_reports) and all(
        item["valid"] for item in comparisons
    )
    return {
        "schema": REPORT_SCHEMA,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_data),
        "capability": manifest.capability,
        "provenance": manifest.provenance,
        "protocol": {
            "magic": manifest.protocol.magic,
            "context": manifest.protocol.context,
            "container": manifest.protocol.container,
        },
        "strict": effective_strict,
        "captures": capture_reports,
        "comparisons": comparisons,
        "valid": valid,
    }


def report_json(report: dict[str, Any]) -> str:
    """Serialize an analysis report deterministically."""
    return (
        json.dumps(
            report,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def manifest_json(manifest: dict[str, JsonValue]) -> str:
    """Validate and serialize an experiment manifest deterministically."""
    parse_manifest(manifest)
    return json.dumps(manifest, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _write_report(
    content: str,
    output: Path | None,
    force: bool,
) -> None:
    if output is None:
        print(content, end="")
    else:
        atomic_write_text(output, content, force=force)


def _require_safe_report_output(
    manifest_path: Path,
    manifest: ExperimentManifest,
    output_path: Path,
) -> None:
    manifest_target = manifest_path.resolve(strict=True)
    root = manifest_target.parent
    protected = [("manifest", manifest_target)]
    protected.extend(
        (
            f"capture {spec.identifier!r}",
            _resolve_capture_path(root, spec, strict=False),
        )
        for spec in manifest.captures
    )
    protected.extend(_provenance_evidence_paths(root, manifest.provenance, "manifest"))
    for spec in manifest.captures:
        protected.extend(
            _provenance_evidence_paths(
                root,
                spec.provenance,
                f"capture {spec.identifier!r}",
            )
        )
    output_target = output_path.resolve(strict=False)
    for label, target in protected:
        same_target = output_target == target
        same_file = (
            output_path.exists() and target.exists() and output_path.samefile(target)
        )
        if same_target or same_file:
            raise ValueError(f"Output would overwrite experiment {label}")


def _provenance_evidence_paths(
    root: Path,
    value: JsonValue,
    label: str,
) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []

    def visit(item: JsonValue, location: str) -> None:
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{location}[{index}]")
            return
        if not isinstance(item, dict):
            return
        files = item.get("files")
        if files is not None:
            if not isinstance(files, dict):
                raise TypeError(f"{location}.files must be an object")
            for name in files:
                paths.append(
                    (
                        f"{location}.files[{name!r}]",
                        _resolve_evidence_path(root, name, location),
                    )
                )
        for name, child in item.items():
            child_location = f"{location}.{name}"
            if name in ("filename", "profile_filename", "project"):
                if not isinstance(child, str):
                    raise TypeError(f"{child_location} must be a string")
                paths.append(
                    (
                        child_location,
                        _resolve_evidence_path(root, child, child_location),
                    )
                )
            visit(child, child_location)

    visit(value, label)
    return paths


def _resolve_evidence_path(root: Path, value: str, label: str) -> Path:
    if not value or "\x00" in value:
        raise ValueError(f"{label} must name a non-empty evidence path")
    relative = Path(value)
    if relative.is_absolute() or PureWindowsPath(value).drive:
        raise ValueError(f"{label} must be relative to the manifest")
    if "\\" in value or ".." in relative.parts:
        raise ValueError(f"{label} must not escape the manifest")
    target = (root / relative).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes the manifest directory") from error
    return target


def _add_analyze_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        help="reject all decode issues and opaque records",
    )
    strict_group.add_argument(
        "--permissive",
        dest="strict",
        action="store_false",
        help="allow losslessly retained opaque records",
    )
    parser.set_defaults(strict=None)


def _add_derive_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "capabilities",
        type=Path,
        help="captured capabilities.json",
    )
    parser.add_argument("family", help="capability family identifier")
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON filename beside capabilities.json",
    )
    parser.add_argument("--force", action="store_true")


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and compare a Ruida capability experiment.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    analyze_parser = commands.add_parser(
        "analyze",
        help="analyze an experiment manifest",
    )
    _add_analyze_arguments(analyze_parser)
    derive_parser = commands.add_parser(
        "derive",
        help="derive one family from captured capabilities",
    )
    _add_derive_arguments(derive_parser)
    parser.epilog = (
        "Legacy analyze syntax without the 'analyze' command remains supported."
    )
    return parser


def _analyze_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and compare a Ruida capability experiment.",
    )
    _add_analyze_arguments(parser)
    return parser


def _run_analyze(args: argparse.Namespace) -> None:
    output_is_safe = False
    try:
        if args.output is not None:
            manifest = load_manifest(args.manifest)
            _require_safe_report_output(
                args.manifest,
                manifest,
                args.output,
            )
        output_is_safe = True
        report = analyze(args.manifest, strict=args.strict)
        _write_report(report_json(report), args.output, args.force)
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        failure = {
            "schema": REPORT_SCHEMA,
            "manifest": str(args.manifest),
            "valid": False,
            "error": f"{type(error).__name__}: {error}",
        }
        content = report_json(failure)
        try:
            output = args.output if output_is_safe else None
            _write_report(content, output, args.force)
        except OSError:
            _write_report(content, None, False)
        raise SystemExit(2) from error
    if not report["valid"]:
        raise SystemExit(1)


def _derived_output_path(
    capability_path: Path,
    family: str,
    requested: Path | None,
) -> Path:
    root = capability_path.resolve(strict=True).parent
    display_root = root if capability_path.is_symlink() else capability_path.parent
    if requested is None:
        output = display_root / f"{family}.experiment.json"
    elif requested.is_absolute():
        output = requested
    else:
        output = display_root / requested
    target = output.resolve(strict=False)
    if target.parent != root:
        raise ValueError("Derived manifest output must remain beside capabilities.json")
    if target.suffix.lower() != ".json":
        raise ValueError("Derived manifest output must be a .json file")
    return output


def _run_derive(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    try:
        family = _identifier(args.family, "Capability family")
        capability_path = args.capabilities
        capability_path.resolve(strict=True)
        source = load_json(capability_path.read_text(encoding="utf-8"))
        document = manifest_from_capability_fixture(source, family)
        output = _derived_output_path(
            capability_path,
            family,
            args.output,
        )
        manifest = parse_manifest(document)
        _require_safe_report_output(
            capability_path,
            manifest,
            output,
        )
        atomic_write_text(
            output,
            manifest_json(document),
            force=args.force,
        )
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        parser.error(f"{type(error).__name__}: {error}")
    print(output)


def main() -> None:
    arguments = sys.argv[1:]
    if not arguments:
        _command_parser().print_help()
        return
    if arguments[0] not in ("analyze", "derive", "-h", "--help"):
        parser = _analyze_parser()
        _run_analyze(parser.parse_args(arguments))
        return
    parser = _command_parser()
    args = parser.parse_args(arguments)
    if args.command == "analyze":
        _run_analyze(args)
    elif args.command == "derive":
        _run_derive(parser, args)


if __name__ == "__main__":
    main()
