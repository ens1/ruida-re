"""Command-line entry point for translating JSON back to Ruida files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cli_io import atomic_write_bytes, require_distinct_paths
from .program import Program


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Encode ruida-re JSON to a scrambled Ruida stream.",
    )
    parser.add_argument("input", type=Path, help="input ruida-re JSON")
    parser.add_argument("output", nargs="?", type=Path, help="output binary")
    parser.add_argument(
        "--checksum",
        choices=("preserve", "recompute"),
        default="preserve",
    )
    parser.add_argument(
        "--container",
        choices=("rd", "udp", "logical"),
        help="override the container recorded in JSON",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-stdout", action="store_true")
    args = parser.parse_args()
    try:
        if args.output is not None:
            require_distinct_paths(args.input, args.output)
        program = Program.from_json(args.input.read_text(encoding="utf-8"))
        if args.container is not None:
            program.container = args.container
        encoded = program.encode(checksum_policy=args.checksum)
        if args.output is None:
            if sys.stdout.isatty() and not args.force_stdout:
                raise ValueError(
                    "Refusing to write binary data to a terminal; "
                    "use an output path or --force-stdout"
                )
            sys.stdout.buffer.write(encoded)
        else:
            atomic_write_bytes(args.output, encoded, force=args.force)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
