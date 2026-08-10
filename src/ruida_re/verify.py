"""Verify exact and structured translation of an external fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .program import KnownCommand, Program, RawSpan, decode


def verify(
    path: Path,
    *,
    magic: int = 0x88,
    context: str = "job",
    container: str = "rd",
    expected_sha256: str | None = None,
) -> dict[str, object]:
    raw_data = path.read_bytes()
    digest = hashlib.sha256(raw_data).hexdigest()
    program = decode(
        raw_data,
        magic=magic,
        context=context,
        container=container,
    )
    direct_exact = program.encode() == raw_data
    json_exact = Program.from_json(program.to_json()).encode() == raw_data
    known = sum(
        isinstance(record, KnownCommand) for record in program.records
    )
    opaque = sum(isinstance(record, RawSpan) for record in program.records)
    sha_matches = (
        expected_sha256 is None
        or digest == expected_sha256.strip().lower()
    )
    return {
        "path": str(path),
        "size": len(raw_data),
        "sha256": digest,
        "sha256_matches": sha_matches,
        "context": context,
        "container": container,
        "records": len(program.records),
        "known_records": known,
        "opaque_records": opaque,
        "issues": program.issues,
        "direct_exact": direct_exact,
        "json_exact": json_exact,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--magic",
        type=lambda value: int(value, 0),
        default=0x88,
    )
    parser.add_argument(
        "--context",
        choices=("job", "request", "reply"),
        default="job",
    )
    parser.add_argument(
        "--container",
        choices=("rd", "udp", "logical"),
        default="rd",
    )
    parser.add_argument("--expected-sha256")
    parser.add_argument("--require-structured", action="store_true")
    args = parser.parse_args()
    result = verify(
        args.path,
        magic=args.magic,
        context=args.context,
        container=args.container,
        expected_sha256=args.expected_sha256,
    )
    print(json.dumps(result, indent=2))
    valid = bool(
        result["sha256_matches"]
        and result["direct_exact"]
        and result["json_exact"]
    )
    if args.require_structured:
        valid = valid and result["opaque_records"] == 0
    if not valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
