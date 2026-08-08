from __future__ import annotations

import os
import tempfile
from pathlib import Path


def default_output_path(source: Path) -> Path:
    suffixes = "".join(source.suffixes)
    if suffixes:
        stem = source.name[: -len(suffixes)]
        return source.with_name(f"{stem}.cleaned{suffixes}")
    return source.with_name(source.name + ".cleaned")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
