"""Safe filesystem output helpers shared by command-line tools."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def require_distinct_paths(input_path: Path, output_path: Path) -> None:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different")


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    force: bool = False,
) -> None:
    """Atomically create a file, refusing replacement unless requested."""
    directory = path.parent
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=directory,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError as error:
                raise FileExistsError(path) from error
            temporary_path.unlink()
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    force: bool = False,
) -> None:
    atomic_write_bytes(path, content.encode("utf-8"), force=force)
