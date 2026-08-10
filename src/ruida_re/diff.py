"""Compare Ruida files after removing their byte scrambling."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .codec import unswizzle
from .program import KnownCommand, decode, split_wrapper


@dataclass(frozen=True)
class Change:
    """One changed region in two unscrambled streams."""

    operation: str
    before_start: int
    before_end: int
    after_start: int
    after_end: int
    before: bytes
    after: bytes


def compare(
    before: bytes,
    after: bytes,
    magic: int = 0x88,
) -> list[Change]:
    """Return changed regions from two scrambled Ruida files."""
    _, before_body = split_wrapper(before)
    _, after_body = split_wrapper(after)
    left = unswizzle(before_body, magic)
    right = unswizzle(after_body, magic)
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    return [
        Change(tag, i1, i2, j1, j2, left[i1:i2], right[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _hex(data: bytes, limit: int = 48) -> str:
    shown = data[:limit].hex(" ").upper()
    if len(data) > limit:
        return f"{shown} ... ({len(data)} bytes)"
    return shown or "-"


def _print_commands(
    label: str,
    raw_data: bytes,
    magic: int,
    context: str,
) -> None:
    print(f"{label} command frames:")
    for record in decode(raw_data, magic=magic, context=context).records:
        if isinstance(record, KnownCommand):
            values = json.dumps(record.values, sort_keys=True)
            print(
                f"  {record.offset:04d} {record.opcode} "
                f"{record.name} {values}"
            )
        else:
            print(f"  {record.offset:04d} opaque {record.raw}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument(
        "--commands",
        action="store_true",
        help="also print commands recognized in each file",
    )
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
    args = parser.parse_args()
    before = args.before.read_bytes()
    after = args.after.read_bytes()

    if args.commands:
        _print_commands("before", before, args.magic, args.context)
        _print_commands("after", after, args.magic, args.context)

    changes = compare(before, after, args.magic)
    if not changes:
        print("No differences after unscrambling.")
        return
    for change in changes:
        print(
            f"{change.operation}: "
            f"before[{change.before_start}:{change.before_end}] "
            f"after[{change.after_start}:{change.after_end}]"
        )
        print(f"  - {_hex(change.before)}")
        print(f"  + {_hex(change.after)}")


if __name__ == "__main__":
    main()
