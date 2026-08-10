"""Generate controlled LightBurn raster discovery projects."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from xml.etree import ElementTree as ET
import zlib

from .cli_io import atomic_write_bytes, atomic_write_text
from .fixture import (
    DEFAULT_FIXTURE_ROOT,
    LIGHTBURN_APP_SHA256,
    project_stage,
)


RASTER_DIR = DEFAULT_FIXTURE_ROOT / "raster"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

BINARY_PIXELS = (
    (255, 0, 0, 255, 0, 255, 255, 255),
    (255, 255, 0, 0, 0, 255, 0, 255),
    (0, 255, 255, 0, 0, 255, 255, 255),
    (255, 0, 255, 255, 0, 0, 0, 255),
)

GRAYSCALE_PIXELS = (
    (255, 224, 192, 160, 128, 96, 64, 0),
    (128, 0, 224, 64, 255, 160, 96, 192),
)


@dataclass(frozen=True)
class RasterCase:
    """One controlled LightBurn bitmap export."""

    identifier: str
    purpose: str
    pixels: tuple[tuple[int, ...], ...]
    width_mm: float
    height_mm: float
    dither_mode: str
    min_power_percent: float
    max_power_percent: float
    bidirectional: bool = False
    angle_degrees: float = 0
    interval_mm: float = 0.5
    num_passes: int = 1
    z_per_pass_mm: float = 0
    speed_mm_s: float = 100

    @property
    def pixel_width(self) -> int:
        return len(self.pixels[0])

    @property
    def pixel_height(self) -> int:
        return len(self.pixels)


CASES = (
    RasterCase(
        "r001-threshold-horizontal-unidirectional",
        "binary scanline runs in one horizontal direction",
        BINARY_PIXELS,
        4,
        2,
        "threshold",
        50,
        50,
    ),
    RasterCase(
        "r002-threshold-horizontal-bidirectional",
        "alternating horizontal scan direction",
        BINARY_PIXELS,
        4,
        2,
        "threshold",
        50,
        50,
        bidirectional=True,
    ),
    RasterCase(
        "r003-threshold-vertical-unidirectional",
        "vertical scan axis and layer mode",
        BINARY_PIXELS,
        4,
        2,
        "threshold",
        50,
        50,
        angle_degrees=90,
    ),
    RasterCase(
        "r004-threshold-interval-025",
        "scan interval and resampled motion spacing",
        BINARY_PIXELS,
        4,
        2,
        "threshold",
        50,
        50,
        interval_mm=0.25,
    ),
    RasterCase(
        "r005-grayscale-range-10-90",
        "grayscale sample-to-power mapping",
        GRAYSCALE_PIXELS,
        4,
        1,
        "grayscale",
        10,
        90,
    ),
    RasterCase(
        "r006-grayscale-range-30-90",
        "grayscale minimum-power transfer",
        GRAYSCALE_PIXELS,
        4,
        1,
        "grayscale",
        30,
        90,
    ),
    RasterCase(
        "r007-3d-slice-four-pass",
        "four-pass depth slicing over the grayscale ramp",
        GRAYSCALE_PIXELS,
        4,
        1,
        "3dslice",
        50,
        50,
        num_passes=4,
    ),
    RasterCase(
        "r008-threshold-vertical-bidirectional",
        "alternating vertical scan direction",
        BINARY_PIXELS,
        4,
        2,
        "threshold",
        50,
        50,
        bidirectional=True,
        angle_degrees=90,
    ),
    RasterCase(
        "r009-3d-slice-four-pass-z-step-05",
        "four-pass depth slicing with 0.5 mm Z steps",
        GRAYSCALE_PIXELS,
        4,
        1,
        "3dslice",
        50,
        50,
        num_passes=4,
        z_per_pass_mm=0.5,
    ),
)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", checksum)
    )


def encode_grayscale_png(
    pixels: tuple[tuple[int, ...], ...],
) -> bytes:
    """Encode a rectangular 8-bit grayscale matrix as deterministic PNG."""
    if not pixels or not pixels[0]:
        raise ValueError("Pixel matrix must not be empty")
    width = len(pixels[0])
    for row in pixels:
        if len(row) != width:
            raise ValueError("Pixel rows must have equal width")
        if any(value < 0 or value > 255 for value in row):
            raise ValueError("Pixels must be in the range 0 through 255")
    height = len(pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    scanlines = b"".join(b"\x00" + bytes(row) for row in pixels)
    return b"".join(
        (
            PNG_SIGNATURE,
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(scanlines, level=9)),
            _png_chunk(b"IEND", b""),
        )
    )


def _value(parent: ET.Element, name: str, value: object) -> None:
    ET.SubElement(parent, name, Value=str(value))


def build_project(case: RasterCase) -> ET.Element:
    """Build one synthetic bitmap with controlled image-layer settings."""
    project = ET.Element(
        "LightBurnProject",
        AppVersion="2.1.03",
        FormatVersion="1",
        MaterialHeight="0",
        MirrorX="False",
        MirrorY="False",
    )
    setting = ET.SubElement(project, "CutSetting_Img", type="Image")
    values = {
        "index": 0,
        "name": "C00",
        "minPower": case.min_power_percent,
        "maxPower": case.max_power_percent,
        "minPower2": case.min_power_percent,
        "maxPower2": case.max_power_percent,
        "speed": case.speed_mm_s,
        "enableLaser1": 1,
        "enableLaser2": 0,
        "priority": 0,
        "doOutput": 1,
        "runBlower": 0,
        "autoBlower": 0,
        "numPasses": case.num_passes,
        "zPerPass": case.z_per_pass_mm,
        "scanOpt": "individual",
        "bidir": int(case.bidirectional),
        "crossHatch": 0,
        "overscan": 0,
        "overscanPercent": 0,
        "interval": case.interval_mm,
        "angle": case.angle_degrees,
        "negative": 0,
        "passThrough": 0,
        "ditherMode": case.dither_mode,
        "cleanupPass": 0,
        "dpi": 25.4 / case.interval_mm,
        "linkDPItoInterval": 1,
    }
    for name, value in values.items():
        _value(setting, name, value)

    png = encode_grayscale_png(case.pixels)
    shape = ET.SubElement(
        project,
        "Shape",
        Type="Bitmap",
        CutIndex="0",
        W=f"{case.width_mm:g}",
        H=f"{case.height_mm:g}",
        Gamma="1",
        Contrast="0",
        Brightness="0",
        EnhanceAmount="0",
        EnhanceRadius="0",
        EnhanceDenoise="0",
        File=f"{case.identifier}.png",
        SourceHash="0",
        Data=base64.b64encode(png).decode("ascii"),
    )
    center_x = 20 + case.width_mm / 2
    center_y = 20 + case.height_mm / 2
    ET.SubElement(shape, "XForm").text = (
        f"1 0 0 1 {center_x:g} {center_y:g}"
    )
    ET.SubElement(project, "Notes", ShowOnLoad="0", Notes="")
    return project


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _case_manifest(
    case: RasterCase,
    directory: Path,
) -> dict[str, object]:
    png = encode_grayscale_png(case.pixels)
    item: dict[str, object] = {
        "identifier": case.identifier,
        "purpose": case.purpose,
        "project": f"{case.identifier}.lbrn2",
        "expected_rd": f"{case.identifier}.rd",
        "export_status": (
            "captured"
            if (directory / f"{case.identifier}.rd").is_file()
            else "pending"
        ),
        "bitmap": {
            "width_pixels": case.pixel_width,
            "height_pixels": case.pixel_height,
            "width_mm": case.width_mm,
            "height_mm": case.height_mm,
            "top_left_mm": [20, 20],
            "pixels": case.pixels,
            "embedded_png_sha256": _sha256_bytes(png),
        },
        "layer": {
            "mode": case.dither_mode,
            "speed_mm_s": case.speed_mm_s,
            "min_power_percent": case.min_power_percent,
            "max_power_percent": case.max_power_percent,
            "bidirectional": case.bidirectional,
            "angle_degrees": case.angle_degrees,
            "interval_mm": case.interval_mm,
            "dpi": 25.4 / case.interval_mm,
            "overscan": False,
            "num_passes": case.num_passes,
            "z_per_pass_mm": case.z_per_pass_mm,
        },
        "files": {},
    }
    files = item["files"]
    if not isinstance(files, dict):
        raise AssertionError(files)
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
    path = directory / "raster.json"
    atomic_write_text(
        path,
        json.dumps(_manifest(directory), indent=2, sort_keys=True) + "\n",
        force=force,
    )
    return path


def generate(directory: Path = RASTER_DIR, force: bool = False) -> None:
    """Write raster projects and a pending provenance manifest."""
    targets = [
        directory / f"{case.identifier}.lbrn2" for case in CASES
    ]
    targets.append(directory / "raster.json")
    for path in targets:
        if path.exists() and not force:
            raise FileExistsError(path)
    directory.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        tree = ET.ElementTree(build_project(case))
        ET.indent(tree, space="    ")
        content = ET.tostring(
            tree.getroot(),
            encoding="utf-8",
            xml_declaration=True,
        )
        atomic_write_bytes(
            directory / f"{case.identifier}.lbrn2",
            content,
            force=force,
        )
    print(_write_manifest(directory, force=force))


def record(directory: Path = RASTER_DIR) -> None:
    """Record hashes after every raster project has an RD export."""
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
    parser.add_argument("--directory", type=Path, default=RASTER_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.directory, force=args.force)
    else:
        record(args.directory)


if __name__ == "__main__":
    main()
