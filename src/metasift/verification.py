from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .engine import inspect_file
from .models import CleanMode, VerificationReport
from .policy import should_remove
from .provenance import inspect_provenance
from .resource_limits import DEFAULT_BUDGET, ResourceBudget


def _exiftool_check(path: Path) -> dict[str, Any]:
    executable = shutil.which("exiftool")
    if executable is None:
        return {"backend": "ExifTool", "status": "unavailable"}
    try:
        completed = subprocess.run(
            [executable, "-j", "-G1", "-a", "-s", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        rows = json.loads(completed.stdout)
        row = rows[0] if rows else {}
        ignored = {"SourceFile", "FileName", "Directory", "FileSize", "FileModifyDate", "FileAccessDate", "FileInodeChangeDate", "FilePermissions", "FileType", "FileTypeExtension", "MIMEType"}
        metadata_keys = sorted(key for key in row if key.split("]", 1)[-1] not in ignored)
        return {"backend": "ExifTool", "status": "ok", "metadata_key_count": len(metadata_keys), "sample_keys": metadata_keys[:30]}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        return {"backend": "ExifTool", "status": "error", "error": str(exc)}


def _pillow_check(path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("PIL") is None:
        return {"backend": "Pillow", "status": "unavailable"}
    try:
        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif()
            observed = {
                "format": image.format,
                "exif_entry_count": len(exif),
                "metadata_info_keys": sorted(
                    key for key in image.info if key.casefold() in {"exif", "xmp", "icc_profile", "comment", "parameters"}
                ),
            }
        return {"backend": "Pillow", "status": "ok", "observed": observed}
    except Exception as exc:
        return {"backend": "Pillow", "status": "error", "error": str(exc)}


def _mutagen_check(path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("mutagen") is None:
        return {"backend": "Mutagen", "status": "unavailable"}
    try:
        if path.suffix.casefold() == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError

            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tag_count = 0
            else:
                tag_count = len(tags)
            return {"backend": "Mutagen", "status": "ok", "tag_count": tag_count}

        import mutagen

        asset = mutagen.File(path)
        tag_count = len(asset.tags) if asset is not None and asset.tags is not None else 0
        return {"backend": "Mutagen", "status": "ok", "tag_count": tag_count}
    except Exception as exc:
        return {"backend": "Mutagen", "status": "error", "error": str(exc)}


def verify_file(
    path: str | Path,
    *,
    mode: CleanMode | str = CleanMode.SHARE_SAFE,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    independent: bool = True,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> VerificationReport:
    source = Path(path)
    clean_mode = mode if isinstance(mode, CleanMode) else CleanMode(mode)
    report = inspect_file(source, budget=budget)
    if report.adapter == "generic":
        return VerificationReport(
            path=source,
            mode=clean_mode,
            supported=False,
            clean=None,
            remaining=(),
            warnings=report.warnings,
        )
    remaining = tuple(
        entry for entry in report.metadata if should_remove(entry, clean_mode, remove_keys, keep_keys)
    )
    checks: list[dict[str, Any]] = []
    if independent:
        checks.append(_exiftool_check(source))
        if report.format in {"JPEG", "PNG", "WebP"} or source.suffix.casefold() == ".avif":
            checks.append(_pillow_check(source))
        if report.format == "MP3":
            checks.append(_mutagen_check(source))
        if any(entry.provenance_related for entry in report.metadata) or clean_mode in {CleanMode.PROVENANCE, CleanMode.AI, CleanMode.METADATA_MAX, CleanMode.FULL}:
            provenance = inspect_provenance(source, budget=budget)
            checks.append({
                "backend": "c2pa-python",
                "status": provenance.c2pa_status,
                "available": provenance.c2pa_available,
                "structural_signal_count": len(provenance.structural_signals),
            })
    warnings = list(report.warnings)
    if independent and all(check.get("status") == "unavailable" for check in checks):
        warnings.append("No independent verification backend was available; clean status is based on MetaSift's native parser only.")
    return VerificationReport(
        path=source,
        mode=clean_mode,
        supported=True,
        clean=not remaining,
        remaining=remaining,
        independent_checks=tuple(checks),
        warnings=tuple(warnings),
    )
