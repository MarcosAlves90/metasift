from __future__ import annotations

import argparse
import stat
import tomllib
import zipfile
from pathlib import Path

EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "release-assets",
}
EXCLUDED_NAMES = {".coverage"}


def include(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
        return False
    if path.name in EXCLUDED_NAMES or path.suffix in {".pyc", ".pyo"}:
        return False
    return path.is_file()


def package(root: Path, destination: Path) -> None:
    root = root.resolve()
    destination = destination.resolve()
    files = sorted(path for path in root.rglob("*") if include(path, root))
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    prefix = f"{project['name']}-{project['version']}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"{prefix}/{relative}", date_time=(2026, 8, 8, 0, 0, 0))
            mode = path.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info.external_attr = permissions << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    package(Path(__file__).resolve().parents[1], args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
