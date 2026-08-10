"""Generate advanced LightBurn projects for protocol discovery."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from .cli_io import atomic_write_bytes, atomic_write_text
from .fixture import (
    DEFAULT_FIXTURE_ROOT,
    LIGHTBURN_APP_SHA256,
    build_project,
    project_stage,
)


ADVANCED_DIR = DEFAULT_FIXTURE_ROOT / "advanced"


def _set_value(element: ET.Element, name: str, value: object) -> None:
    child = element.find(name)
    if child is None:
        raise ValueError(f"Missing LightBurn value {name}")
    child.set("Value", str(value))


def build_multilayer_project() -> ET.Element:
    project = build_project()
    first_setting = project.find("CutSetting")
    first_shape = project.find("Shape")
    if first_setting is None or first_shape is None:
        raise ValueError("Baseline project structure is incomplete")

    second_setting = copy.deepcopy(first_setting)
    for name, value in {
        "index": 1,
        "name": "C01",
        "minPower": 30,
        "maxPower": 40,
        "minPower2": 30,
        "maxPower2": 40,
        "speed": 15,
        "runBlower": 1,
        "autoBlower": 1,
    }.items():
        _set_value(second_setting, name, value)

    second_shape = copy.deepcopy(first_shape)
    second_shape.set("CutIndex", "1")
    xform = second_shape.find("XForm")
    if xform is None:
        raise ValueError("Baseline shape has no transform")
    xform.text = "1 0 0 1 20 30"

    project.insert(1, second_setting)
    notes = project.find("Notes")
    if notes is None:
        raise ValueError("Baseline project has no notes marker")
    notes_index = list(project).index(notes)
    project.insert(notes_index, second_shape)
    return project


def build_relative_project() -> ET.Element:
    project = build_project()
    shape = project.find("Shape")
    if shape is None:
        raise ValueError("Baseline project has no shape")
    vertices = (
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (3, 1),
        (3, 2),
        (2, 2),
        (1, 2),
        (0, 2),
        (0, 1),
        (0, 0),
    )
    vert_list = shape.find("VertList")
    prim_list = shape.find("PrimList")
    if vert_list is None or prim_list is None:
        raise ValueError("Baseline path structure is incomplete")
    vert_list.text = " ".join(f"V {x} {y}" for x, y in vertices)
    prim_list.text = " ".join(
        f"L {index} {index + 1}" for index in range(len(vertices) - 1)
    )
    return project


def build_negative_project() -> ET.Element:
    return build_project(start_mm=(-1, 20), end_mm=(1, 20))


BUILDERS = {
    "a001-multilayer": (
        "two layers with distinct speed, power, color, and air",
        build_multilayer_project,
    ),
    "a002-relative-polyline": (
        "short horizontal and vertical segments in every direction",
        build_relative_project,
    ),
    "a003-negative-coordinate": (
        "line crossing X zero to establish signed coordinate spelling",
        build_negative_project,
    ),
}


BLOCKED_EXPORTS = {
    "a003-negative-coordinate": (
        "LightBurn 2.1.03 with the recorded Ruida 644XS profile reports "
        "that no shape is inside the machine work area."
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(directory: Path) -> dict[str, object]:
    cases = []
    for identifier, (purpose, _) in BUILDERS.items():
        rd_path = directory / f"{identifier}.rd"
        item: dict[str, object] = {
            "identifier": identifier,
            "purpose": purpose,
            "project": f"{identifier}.lbrn2",
            "expected_rd": f"{identifier}.rd",
            "export_status": (
                "captured"
                if rd_path.is_file()
                else (
                    "blocked"
                    if identifier in BLOCKED_EXPORTS
                    else "pending"
                )
            ),
            "files": {},
        }
        if identifier in BLOCKED_EXPORTS and not rd_path.is_file():
            item["export_note"] = BLOCKED_EXPORTS[identifier]
        files = item["files"]
        if not isinstance(files, dict):
            raise AssertionError(files)
        for suffix in ("lbrn2", "rd"):
            path = directory / f"{identifier}.{suffix}"
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
        cases.append(item)
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
        "cases": cases,
    }


def _write_manifest(directory: Path, force: bool = False) -> Path:
    path = directory / "advanced.json"
    atomic_write_text(
        path,
        json.dumps(_manifest(directory), indent=2, sort_keys=True) + "\n",
        force=force,
    )
    return path


def generate(directory: Path = ADVANCED_DIR, force: bool = False) -> None:
    targets = [
        directory / f"{identifier}.lbrn2" for identifier in BUILDERS
    ]
    targets.append(directory / "advanced.json")
    for path in targets:
        if path.exists() and not force:
            raise FileExistsError(path)
    directory.mkdir(parents=True, exist_ok=True)
    for identifier, (_, builder) in BUILDERS.items():
        tree = ET.ElementTree(builder())
        ET.indent(tree, space="    ")
        content = ET.tostring(
            tree.getroot(),
            encoding="utf-8",
            xml_declaration=True,
        )
        atomic_write_bytes(
            directory / f"{identifier}.lbrn2",
            content,
            force=force,
        )
    print(_write_manifest(directory, force=force))


def record(directory: Path = ADVANCED_DIR) -> None:
    for identifier in BUILDERS:
        path = directory / f"{identifier}.lbrn2"
        if not path.is_file():
            raise FileNotFoundError(path)
    print(_write_manifest(directory, force=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "record"))
    parser.add_argument("--directory", type=Path, default=ADVANCED_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.directory, force=args.force)
    else:
        record(args.directory)


if __name__ == "__main__":
    main()
