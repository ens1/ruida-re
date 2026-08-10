"""Command-line entry point for translating Ruida files to JSON."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cli_io import atomic_write_text, require_distinct_paths
from .program import decode_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decode a scrambled Ruida stream to lossless JSON.",
    )
    parser.add_argument("input", type=Path, help="input .rd or wire payload")
    parser.add_argument("output", nargs="?", type=Path, help="output JSON")
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
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return a failure status when any frame remains opaque",
    )
    args = parser.parse_args()
    try:
        if args.output is not None:
            require_distinct_paths(args.input, args.output)
        program = decode_path(
            args.input,
            args.magic,
            args.context,
            args.container,
        )
        content = program.to_json(indent=None if args.compact else 2)
        if args.output is None:
            sys.stdout.write(content)
        else:
            atomic_write_text(args.output, content, force=args.force)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    if program.issues:
        print(
            f"warning: {len(program.issues)} opaque or invalid frame(s)",
            file=sys.stderr,
        )
        if args.strict:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
