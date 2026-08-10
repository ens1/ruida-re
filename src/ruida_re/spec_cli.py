"""Print the declarative protocol registry as JSON."""

from __future__ import annotations

import argparse
from collections import Counter
import json

from .registry import (
    CATALOG_SOURCES,
    REGISTRY_CONTEXT_EVIDENCE,
    get_registry,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--context",
        choices=("job", "request", "reply"),
        default="job",
    )
    args = parser.parse_args()
    registry = get_registry(args.context)
    rows = [
        {
            "opcode": spec.opcode.hex(),
            "name": spec.name,
            "shape_evidence": spec.shape_evidence,
            "semantic_evidence": spec.semantic_evidence,
            "shape_sources": list(spec.shape_sources),
            "semantic_sources": list(spec.semantic_sources),
            "notes": spec.notes,
            "fields": [
                {
                    "name": field.name,
                    "codec": type(field).__name__,
                }
                for field in spec.fields
            ],
        }
        for spec in registry
    ]
    result = {
        "context": args.context,
        "context_membership_evidence": REGISTRY_CONTEXT_EVIDENCE[
            args.context
        ],
        "shape_evidence_counts": dict(
            sorted(Counter(row["shape_evidence"] for row in rows).items())
        ),
        "catalog_sources": list(CATALOG_SOURCES),
        "commands": rows,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
