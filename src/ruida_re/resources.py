"""Read versioned integration artifacts shipped with :mod:`ruida_re`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Final


CATALOG_V1: Final = "spec/catalog-v1.json"
CATALOG_SCHEMA_V1: Final = "schemas/catalog-v1.schema.json"
CONFORMANCE_V1: Final = "spec/conformance-v1.json"
CONFORMANCE_SCHEMA_V1: Final = "schemas/conformance-v1.schema.json"
PROGRAM_SCHEMA_V1: Final = "schemas/program-v1.schema.json"
TRANSCRIPT_SCHEMA_V1: Final = "schemas/transcript-v1.schema.json"

ARTIFACTS: Final = (
    CATALOG_V1,
    CATALOG_SCHEMA_V1,
    CONFORMANCE_V1,
    CONFORMANCE_SCHEMA_V1,
    PROGRAM_SCHEMA_V1,
    TRANSCRIPT_SCHEMA_V1,
)

_RESOURCE_PACKAGE = "ruida_re.data"


def artifact(name: str) -> Traversable:
    """Return a readable packaged artifact without assuming a filesystem."""
    if name not in ARTIFACTS:
        available = ", ".join(ARTIFACTS)
        raise ValueError(
            f"Unknown packaged artifact {name!r}; choose from {available}"
        )
    current = files(_RESOURCE_PACKAGE)
    for component in name.split("/"):
        current = current.joinpath(component)
    return current


def read_artifact_text(name: str) -> str:
    """Read a packaged artifact as UTF-8 text."""
    return artifact(name).read_text(encoding="utf-8")


def read_artifact_bytes(name: str) -> bytes:
    """Read a packaged artifact as bytes."""
    return artifact(name).read_bytes()


def read_artifact_json(name: str) -> dict[str, Any]:
    """Read a packaged artifact as a JSON object."""
    value = json.loads(read_artifact_text(name))
    if not isinstance(value, dict):
        raise ValueError(f"Packaged artifact {name!r} is not a JSON object")
    return value


@contextmanager
def artifact_path(name: str) -> Iterator[Path]:
    """Yield a temporary or installed path for a packaged artifact."""
    with as_file(artifact(name)) as path:
        yield path


__all__ = (
    "ARTIFACTS",
    "CATALOG_SCHEMA_V1",
    "CATALOG_V1",
    "CONFORMANCE_SCHEMA_V1",
    "CONFORMANCE_V1",
    "PROGRAM_SCHEMA_V1",
    "TRANSCRIPT_SCHEMA_V1",
    "artifact",
    "artifact_path",
    "read_artifact_bytes",
    "read_artifact_json",
    "read_artifact_text",
)
