"""Generate controlled LightBurn device profiles for offline research."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cli_io import atomic_write_bytes

MANIFEST_NAME = "lightburn-profile-matrix.json"
_SCHEMA = "ruida-re.lightburn-profile-matrix"
_SCHEMA_VERSION = 1
_SNAPSHOT_SCHEMA = "ruida-re.lightburn-profile-snapshot"
_SNAPSHOT_SCHEMA_VERSION = 1
_GUID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class ProfileVariant:
    """One independently varied LightBurn device setting."""

    identifier: str
    setting: str

    @property
    def filename(self) -> str:
        return f"research-{self.identifier}.lbdev"


VARIANTS = (
    ProfileVariant("enable-z", "EnableZ"),
    ProfileVariant("laser-2-enabled", "Laser2Enabled"),
    ProfileVariant("laser-1-rf-tube", "Laser1IsRFTube"),
    ProfileVariant("laser-1-fiber", "Laser1IsFiber"),
    ProfileVariant("save-rotary-config", "SaveRotaryConfig"),
)


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _canonical_bytes(value: object) -> bytes:
    content = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (content + "\n").encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_json(path: Path, description: str) -> tuple[object, bytes]:
    raw = path.read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid {description} JSON: {error}") from error
    return document, raw


def _parse_source(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.suffix.lower() != ".lbdev":
        raise ValueError(f"Input must end in .lbdev: {path}")
    value, raw = _parse_json(path, "LightBurn profile")
    document = value
    if not isinstance(document, dict):
        raise TypeError("LightBurn profile must be a JSON object")
    device_list = document.get("DeviceList")
    if not isinstance(device_list, list):
        raise TypeError("LightBurn profile must contain a DeviceList array")
    if len(device_list) != 1:
        raise ValueError("LightBurn profile must contain one DeviceList entry")
    device = device_list[0]
    if not isinstance(device, dict):
        raise TypeError("DeviceList entry must be a JSON object")
    controller_name = device.get("Name")
    if not isinstance(controller_name, str):
        raise TypeError("DeviceList entry Name must be a string")
    if controller_name != "Ruida":
        raise ValueError(
            "LightBurn profile controller Name must be exactly 'Ruida'; "
            f"found {controller_name!r}"
        )
    profile_type = device.get("Type")
    if not isinstance(profile_type, str):
        raise TypeError("DeviceList entry Type must be a string")
    display_name = device.get("DisplayName")
    if not isinstance(display_name, str):
        raise TypeError("DeviceList entry DisplayName must be a string")
    if not display_name.strip():
        raise ValueError("DeviceList entry must have a DisplayName")
    guid = device.get("GUID")
    if not isinstance(guid, str):
        raise TypeError("DeviceList entry GUID must be a string")
    if not guid:
        raise ValueError("DeviceList entry must have a GUID")
    settings = device.get("Settings")
    if not isinstance(settings, dict):
        raise TypeError("DeviceList entry must contain a Settings object")
    for variant in VARIANTS:
        if variant.setting not in settings:
            continue
        value = settings[variant.setting]
        if not isinstance(value, bool):
            raise TypeError(f"Settings.{variant.setting} must be a boolean")
        if value:
            raise ValueError(
                f"Settings.{variant.setting} is already true; "
                "the requested profile would not be a controlled change"
            )
    return document, raw


def _selected_ruida_device(
    document: object,
) -> tuple[dict[str, Any], int]:
    if not isinstance(document, dict):
        raise TypeError("LightBurn preferences must be a JSON object")
    device_list = document.get("DeviceList")
    if not isinstance(device_list, list):
        raise TypeError("LightBurn preferences must contain a DeviceList array")
    matches: list[tuple[int, dict[str, Any]]] = []
    for index, device in enumerate(device_list):
        if not isinstance(device, dict):
            raise TypeError(f"DeviceList entry {index} must be a JSON object")
        if device.get("Name") == "Ruida":
            matches.append((index, device))
    if len(matches) != 1:
        raise ValueError(
            "LightBurn preferences must contain exactly one Ruida "
            f"DeviceList entry; found {len(matches)}"
        )
    index, device = matches[0]
    if not isinstance(device.get("Type"), str):
        raise TypeError("Ruida DeviceList entry Type must be a string")
    return device, index


def _paths_alias(input_path: Path, output_path: Path) -> bool:
    if input_path.resolve() == output_path.resolve():
        return True
    try:
        return input_path.samefile(output_path)
    except FileNotFoundError:
        return False


def snapshot(
    prefs_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> dict[str, object]:
    """Extract one Ruida device profile from an explicit preferences file."""
    prefs_path = prefs_path.resolve()
    if output_path.suffix.lower() != ".lbdev":
        raise ValueError(f"Output must end in .lbdev: {output_path}")
    if _paths_alias(prefs_path, output_path):
        raise ValueError("Preferences input and profile output must be different")
    document, raw_prefs = _parse_json(prefs_path, "LightBurn preferences")
    selected_device, selected_index = _selected_ruida_device(document)
    selected_device_content = _canonical_bytes(selected_device)
    output_content = _canonical_bytes({"DeviceList": [deepcopy(selected_device)]})
    provenance: dict[str, object] = {
        "schema": _SNAPSHOT_SCHEMA,
        "schema_version": _SNAPSHOT_SCHEMA_VERSION,
        "prefs_path": str(prefs_path),
        "output_path": str(output_path.resolve()),
        "device_list_index": selected_index,
        "controller_identity": {
            "field": "DeviceList[0].Name",
            "value": "Ruida",
        },
        "profile_type": selected_device["Type"],
        "raw_prefs_sha256": _sha256(raw_prefs),
        "selected_device_sha256": _sha256(selected_device_content),
        "output_sha256": _sha256(output_content),
        "output_size": len(output_content),
    }
    atomic_write_bytes(output_path, output_content, force=force)
    return provenance


def _base62_prefix(content: bytes, length: int = 10) -> str:
    value = int.from_bytes(content, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, len(_GUID_ALPHABET))
        encoded = _GUID_ALPHABET[remainder] + encoded
    return encoded.rjust(length, "0")[:length]


def _derived_guid(
    source_hash: str,
    variant: ProfileVariant,
    used: set[str],
) -> tuple[str, str]:
    derivation = (
        f"{_SCHEMA}:{_SCHEMA_VERSION}\0{source_hash}\0"
        f"{variant.identifier}\0{variant.setting}\0true"
    ).encode()
    derivation_hash = _sha256(derivation)
    attempt = 0
    while True:
        candidate_input = derivation + b"\0" + str(attempt).encode("ascii")
        candidate = _base62_prefix(hashlib.sha256(candidate_input).digest())
        if candidate not in used:
            return candidate, derivation_hash
        attempt += 1


def _build_variant(
    source: dict[str, Any],
    variant: ProfileVariant,
    source_hash: str,
    used_guids: set[str],
) -> tuple[dict[str, Any], dict[str, object]]:
    document = deepcopy(source)
    device = document["DeviceList"][0]
    settings = device["Settings"]
    source_key_present = variant.setting in settings
    source_value = settings.get(variant.setting)
    guid, derivation_hash = _derived_guid(
        source_hash,
        variant,
        used_guids,
    )
    used_guids.add(guid)
    source_name = device["DisplayName"]
    display_name = f"RESEARCH ONLY - {source_name} - {variant.setting}=true"
    device["DisplayName"] = display_name
    device["GUID"] = guid
    settings[variant.setting] = True
    metadata: dict[str, object] = {
        "identifier": variant.identifier,
        "filename": variant.filename,
        "display_name": display_name,
        "guid": guid,
        "changed_key": variant.setting,
        "change_path": [
            "DeviceList",
            0,
            "Settings",
            variant.setting,
        ],
        "source_key_present": source_key_present,
        "source_value": source_value,
        "target_value": True,
        "derivation_sha256": derivation_hash,
    }
    return document, metadata


def generate(
    source_path: Path,
    output_directory: Path,
    *,
    force: bool = False,
) -> Path:
    """Generate controlled profiles and return the manifest path."""
    source_path = source_path.resolve()
    document, raw_source = _parse_source(source_path)
    source_document_hash = _sha256(_canonical_bytes(document))
    targets = [output_directory / item.filename for item in VARIANTS]
    manifest_path = output_directory / MANIFEST_NAME
    targets.append(manifest_path)
    for target in targets:
        if target.resolve() == source_path:
            raise ValueError(f"Output would overwrite input profile: {target}")
        if target.exists() and not force:
            raise FileExistsError(target)

    device = document["DeviceList"][0]
    used_guids = {device["GUID"]}
    prepared: list[tuple[Path, bytes]] = []
    variant_entries: list[dict[str, object]] = []
    for variant in VARIANTS:
        clone, entry = _build_variant(
            document,
            variant,
            source_document_hash,
            used_guids,
        )
        content = _canonical_bytes(clone)
        entry["variant_sha256"] = _sha256(content)
        entry["size"] = len(content)
        prepared.append((output_directory / variant.filename, content))
        variant_entries.append(entry)

    manifest = {
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "source": {
            "filename": source_path.name,
            "display_name": device["DisplayName"],
            "guid": device["GUID"],
            "controller_identity": {
                "field": "DeviceList[0].Name",
                "value": device["Name"],
            },
            "profile_type": device["Type"],
            "source_sha256": _sha256(raw_source),
            "source_document_sha256": source_document_hash,
        },
        "variants": variant_entries,
    }
    prepared.append((manifest_path, _canonical_bytes(manifest)))
    output_directory.mkdir(parents=True, exist_ok=True)
    for target, content in prepared:
        atomic_write_bytes(target, content, force=force)
    return manifest_path


def _generate_main(argv: Sequence[str], *, explicit: bool = False) -> None:
    parser = argparse.ArgumentParser(
        prog=("ruida-lightburn-profile generate" if explicit else None),
        description=(
            "Generate offline LightBurn research profiles from one exported "
            ".lbdev file. This command does not launch or import LightBurn."
        ),
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace this generator's existing output files",
    )
    args = parser.parse_args(argv)
    try:
        manifest_path = generate(
            args.source,
            args.output_directory,
            force=args.force,
        )
    except (OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(manifest_path)


def _snapshot_main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="ruida-lightburn-profile snapshot",
        description=(
            "Extract one Ruida device from an explicit LightBurn preferences "
            "JSON file. This command does not launch or import LightBurn."
        ),
    )
    parser.add_argument("prefs_json", type=Path)
    parser.add_argument("output_profile", type=Path)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace the output profile if it already exists",
    )
    args = parser.parse_args(argv)
    try:
        provenance = snapshot(
            args.prefs_json,
            args.output_profile,
            force=args.force,
        )
    except (OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(provenance, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "snapshot":
        _snapshot_main(args[1:])
        return
    if args and args[0] == "generate":
        _generate_main(args[1:], explicit=True)
        return
    _generate_main(args)


if __name__ == "__main__":
    main()
