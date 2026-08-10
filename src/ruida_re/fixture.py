"""Generate and record controlled LightBurn protocol fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from .cli_io import atomic_write_bytes, atomic_write_text


DEFAULT_FIXTURE_ROOT = Path("work/lightburn-2.1.03")
FIXTURE_DIR = DEFAULT_FIXTURE_ROOT / "vector"
FIXTURE_ID = "v001-single-line"
LIGHTBURN_APP_SHA256 = (
    "909262ec7f67b1accbf42f9905ded18a317febb09202ff8cfa81bc0256f7d02a"
)


def _value(parent: ET.Element, name: str, value: object) -> None:
    ET.SubElement(parent, name, Value=str(value))


def build_project(
    *,
    start_mm: tuple[float, float] = (20, 20),
    end_mm: tuple[float, float] = (30, 20),
    layer: int = 0,
    speed_mm_s: float = 10,
    min_power_percent: float = 10,
    max_power_percent: float = 20,
    air_assist: bool = False,
    output: bool = True,
) -> ET.Element:
    """Build one line with controlled geometry and layer parameters."""
    project = ET.Element(
        "LightBurnProject",
        AppVersion="2.1.03",
        FormatVersion="1",
        MaterialHeight="0",
        MirrorX="False",
        MirrorY="False",
    )
    setting = ET.SubElement(project, "CutSetting", type="Cut")
    values = {
        "index": layer,
        "name": f"C{layer:02d}",
        "minPower": min_power_percent,
        "maxPower": max_power_percent,
        "minPower2": min_power_percent,
        "maxPower2": max_power_percent,
        "speed": speed_mm_s,
        "enableLaser1": 1,
        "enableLaser2": 0,
        "priority": 0,
        "doOutput": int(output),
        "runBlower": int(air_assist),
        "autoBlower": int(air_assist),
        "numPasses": 1,
    }
    for name, value in values.items():
        _value(setting, name, value)

    shape = ET.SubElement(
        project,
        "Shape",
        Type="Path",
        CutIndex=str(layer),
    )
    dx = end_mm[0] - start_mm[0]
    dy = end_mm[1] - start_mm[1]
    ET.SubElement(shape, "VertList").text = f"V 0 0 V {dx:g} {dy:g}"
    ET.SubElement(shape, "PrimList").text = "L 0 1"
    ET.SubElement(shape, "XForm").text = (
        f"1 0 0 1 {start_mm[0]:g} {start_mm[1]:g}"
    )
    ET.SubElement(project, "Notes", ShowOnLoad="0", Notes="")
    return project


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_stage(path: Path) -> str:
    root = ET.parse(path).getroot()
    if root.get("DeviceName"):
        return "post-lightburn-normalized"
    return "generated-input"


def _manifest() -> dict[str, object]:
    return {
        "fixture": FIXTURE_ID,
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
        "geometry": {
            "kind": "line",
            "start_mm": [20, 20],
            "end_mm": [30, 20],
        },
        "layer": {
            "index": 0,
            "mode": "Line",
            "speed_mm_s": 10,
            "min_power_percent": 10,
            "max_power_percent": 20,
            "air_assist": False,
            "output": True,
        },
        "files": {},
    }


def _paths(directory: Path) -> tuple[Path, Path, Path]:
    return (
        directory / f"{FIXTURE_ID}.lbrn2",
        directory / f"{FIXTURE_ID}.rd",
        directory / f"{FIXTURE_ID}.json",
    )


def generate(directory: Path = FIXTURE_DIR, force: bool = False) -> None:
    """Write the baseline project and initial provenance manifest."""
    project_path, _, manifest_path = _paths(directory)
    for path in (project_path, manifest_path):
        if path.exists() and not force:
            raise FileExistsError(path)
    directory.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(build_project())
    ET.indent(tree, space="    ")
    content = ET.tostring(
        tree.getroot(),
        encoding="utf-8",
        xml_declaration=True,
    )
    atomic_write_bytes(project_path, content, force=force)

    manifest = _manifest()
    manifest["files"] = {
        project_path.name: {
            "sha256": _sha256(project_path),
            "stage": project_stage(project_path),
        }
    }
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        force=force,
    )
    print(project_path)


def record(directory: Path = FIXTURE_DIR) -> None:
    """Record hashes after LightBurn has produced the machine file."""
    project_path, rd_path, manifest_path = _paths(directory)
    if not project_path.is_file():
        raise FileNotFoundError(project_path)
    if not rd_path.is_file():
        raise FileNotFoundError(rd_path)

    manifest = _manifest()
    manifest["files"] = {
        path.name: {
            "sha256": _sha256(path),
            "size": path.stat().st_size,
            "stage": (
                project_stage(path)
                if path.suffix == ".lbrn2"
                else "lightburn-machine-export"
            ),
        }
        for path in (project_path, rd_path)
    }
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        force=True,
    )
    print(manifest_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "record"))
    parser.add_argument("--directory", type=Path, default=FIXTURE_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.directory, force=args.force)
    else:
        record(args.directory)


if __name__ == "__main__":
    main()
