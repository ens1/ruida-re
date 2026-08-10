"""Generate one-variable LightBurn projects for protocol discovery."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .cli_io import atomic_write_bytes, atomic_write_text
from .fixture import (
    DEFAULT_FIXTURE_ROOT,
    LIGHTBURN_APP_SHA256,
    build_project,
    project_stage,
)


MATRIX_DIR = DEFAULT_FIXTURE_ROOT / "matrix"


@dataclass(frozen=True)
class Case:
    identifier: str
    purpose: str
    start_mm: tuple[float, float] = (20, 20)
    end_mm: tuple[float, float] = (30, 20)
    layer: int = 0
    speed_mm_s: float = 10
    min_power_percent: float = 10
    max_power_percent: float = 20
    air_assist: bool = False
    output: bool = True

    def project_values(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("identifier")
        values.pop("purpose")
        return values


CASES = (
    Case(
        "m001-power-000",
        "power scaling at 0%",
        min_power_percent=0,
        max_power_percent=0,
    ),
    Case(
        "m002-power-001",
        "power scaling at 1%",
        min_power_percent=1,
        max_power_percent=1,
    ),
    Case(
        "m003-power-050",
        "power scaling at 50%",
        min_power_percent=50,
        max_power_percent=50,
    ),
    Case(
        "m004-power-099",
        "power scaling at 99%",
        min_power_percent=99,
        max_power_percent=99,
    ),
    Case(
        "m005-power-100",
        "power scaling at 100%",
        min_power_percent=100,
        max_power_percent=100,
    ),
    Case("m006-speed-decimal", "speed precision", speed_mm_s=12.345),
    Case("m007-speed-low", "low speed encoding", speed_mm_s=0.1),
    Case("m008-air-on", "air-assist layer flag", air_assist=True),
    Case(
        "m009-layer-1",
        "a lone C01 UI layer is compacted to RD layer 0 with blue color",
        layer=1,
    ),
    Case(
        "m010-vertical",
        "vertical absolute geometry",
        end_mm=(20, 30),
    ),
    Case(
        "m011-offset-x",
        "job and layer X-bound changes",
        start_mm=(21, 20),
        end_mm=(31, 20),
    ),
    Case(
        "m012-offset-y",
        "job and layer Y-bound changes",
        start_mm=(20, 21),
        end_mm=(30, 21),
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_manifest(case: Case, directory: Path) -> dict[str, object]:
    item = asdict(case)
    item["project"] = f"{case.identifier}.lbrn2"
    item["expected_rd"] = f"{case.identifier}.rd"
    files = {}
    for suffix in ("lbrn2", "rd"):
        path = directory / f"{case.identifier}.{suffix}"
        if path.is_file():
            files[path.name] = {
                "sha256": _sha256(path),
                "size": path.stat().st_size,
                "stage": (
                    project_stage(path)
                    if suffix == "lbrn2"
                    else "lightburn-machine-export"
                ),
            }
    item["files"] = files
    return item


def _manifest(directory: Path) -> dict[str, object]:
    return {
        "reference_software": {
            "name": "LightBurn",
            "version": "2.1.03",
            "platform": "macOS",
            "app_sha256": LIGHTBURN_APP_SHA256,
        },
        "device_profile": {
            "display_name": "Ruida 644XS",
            "controller": "Ruida",
            "connection": "Serial",
            "bed_width_mm": 991.1080322265625,
            "bed_height_mm": 599.947998046875,
            "mirror_x": True,
            "mirror_y": True,
        },
        "cases": [_case_manifest(case, directory) for case in CASES],
    }


def _write_manifest(directory: Path, force: bool = False) -> Path:
    manifest_path = directory / "matrix.json"
    atomic_write_text(
        manifest_path,
        json.dumps(_manifest(directory), indent=2, sort_keys=True) + "\n",
        force=force,
    )
    return manifest_path


def generate(directory: Path = MATRIX_DIR, force: bool = False) -> None:
    targets = [
        directory / f"{case.identifier}.lbrn2" for case in CASES
    ]
    targets.append(directory / "matrix.json")
    for path in targets:
        if path.exists() and not force:
            raise FileExistsError(path)
    directory.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        project = build_project(**case.project_values())
        tree = ET.ElementTree(project)
        ET.indent(tree, space="    ")
        path = directory / f"{case.identifier}.lbrn2"
        content = ET.tostring(
            tree.getroot(),
            encoding="utf-8",
            xml_declaration=True,
        )
        atomic_write_bytes(path, content, force=force)
    manifest_path = _write_manifest(directory, force=force)
    print(manifest_path)


def record(directory: Path = MATRIX_DIR) -> None:
    """Record hashes for generated projects and exported machine files."""
    missing = [
        path
        for case in CASES
        for suffix in ("lbrn2", "rd")
        if not (path := directory / f"{case.identifier}.{suffix}").is_file()
    ]
    if missing:
        raise FileNotFoundError(missing[0])
    print(_write_manifest(directory, force=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "record"))
    parser.add_argument("--directory", type=Path, default=MATRIX_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.directory, force=args.force)
    else:
        record(args.directory)


if __name__ == "__main__":
    main()
