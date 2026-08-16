from __future__ import annotations

from pathlib import Path
from types import ModuleType

from .adapters import gif, generic, jpeg, json_document, markdown, mp3, ooxml, pdf, png, riff, webp
from .capabilities import capabilities_for
from .io_utils import atomic_write, default_output_path
from .models import CleanMode, CleanResult, InspectionReport, SanitizationPlan
from .policy import should_remove
from .resource_limits import DEFAULT_BUDGET, ResourceBudget, ensure_file_size

_ADAPTERS: tuple[tuple[str, str, ModuleType], ...] = (
    ("png", "PNG", png),
    ("jpeg", "JPEG", jpeg),
    ("webp", "WebP", webp),
    ("riff", "RIFF", riff),
    ("gif", "GIF", gif),
    ("ooxml", "OOXML", ooxml),
    ("pdf", "PDF", pdf),
    ("json", "JSON", json_document),
    ("markdown", "Markdown", markdown),
    ("mp3", "MP3", mp3),
)


def _select(data: bytes, suffix: str) -> tuple[str, str, ModuleType, tuple[str, ...]]:
    for adapter_name, format_name, module in _ADAPTERS:
        try:
            if module.matches(data, suffix):
                return adapter_name, format_name, module, ()
        except (ValueError, OSError):
            continue
    return (
        "generic",
        suffix.lstrip(".").upper() or "BINARY",
        generic,
        ("No native metadata adapter is available for this format; sanitization is unsupported.",),
    )


def _read_bounded(source: Path, budget: ResourceBudget) -> bytes:
    size = source.stat().st_size
    ensure_file_size(size, budget)
    data = source.read_bytes()
    if len(data) != size:
        raise ValueError("file changed while MetaSift was reading it")
    return data


def inspect_file(path: str | Path, *, budget: ResourceBudget = DEFAULT_BUDGET) -> InspectionReport:
    source = Path(path)
    data = _read_bounded(source, budget)
    adapter_name, format_name, adapter, warnings = _select(data, source.suffix)
    metadata = adapter.inspect(data, budget=budget)
    return InspectionReport(
        path=source,
        format=format_name,
        adapter=adapter_name,
        size=len(data),
        metadata=tuple(metadata),
        warnings=warnings,
        capabilities=capabilities_for(adapter_name),
    )


def plan_file(
    path: str | Path,
    *,
    mode: CleanMode | str = CleanMode.SHARE_SAFE,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> SanitizationPlan:
    clean_mode = mode if isinstance(mode, CleanMode) else CleanMode(mode)
    report = inspect_file(path, budget=budget)
    remove = tuple(entry for entry in report.metadata if should_remove(entry, clean_mode, remove_keys, keep_keys))
    preserve = tuple(entry for entry in report.metadata if entry not in remove)
    warnings = list(report.warnings)
    if any(entry.provenance_related for entry in remove):
        warnings.append("The plan removes provenance/authenticity information, including C2PA when present.")
    if any(entry.preserve_recommended for entry in remove):
        warnings.append("The plan removes metadata that MetaSift recommends preserving for fidelity or interoperability.")
    return SanitizationPlan(
        source=Path(path),
        mode=clean_mode,
        format=report.format,
        adapter=report.adapter,
        remove=remove,
        preserve=preserve,
        warnings=tuple(warnings),
    )


def clean_file(
    path: str | Path,
    *,
    mode: CleanMode | str = CleanMode.SHARE_SAFE,
    destination: str | Path | None = None,
    in_place: bool = False,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> CleanResult:
    source = Path(path)
    clean_mode = mode if isinstance(mode, CleanMode) else CleanMode(mode)
    if in_place and destination is not None:
        raise ValueError("destination and in_place are mutually exclusive")
    if clean_mode is CleanMode.CUSTOM and not remove_keys:
        raise ValueError("custom mode requires at least one remove key")

    data = _read_bounded(source, budget)
    adapter_name, format_name, adapter, warnings = _select(data, source.suffix)
    if adapter_name == "generic":
        raise ValueError(
            f"unsupported format for cleaning: {format_name}; "
            "MetaSift refuses to claim sanitization without a native adapter"
        )
    cleaned, removed, kept = adapter.clean(
        data,
        clean_mode,
        tuple(remove_keys),
        tuple(keep_keys),
        budget=budget,
    )
    ensure_file_size(len(cleaned), budget)

    # Verify against the same policy before committing the atomic output. This is
    # not an independent oracle, but it prevents adapters from reporting success
    # while leaving a target they themselves recognize.
    remaining = tuple(
        entry
        for entry in adapter.inspect(cleaned, budget=budget)
        if should_remove(entry, clean_mode, remove_keys, keep_keys)
    )
    if remaining:
        selectors = ", ".join(entry.selector for entry in remaining[:5])
        raise ValueError(f"post-clean verification failed; target metadata remains: {selectors}")

    if in_place:
        target = source
    elif destination is not None:
        target = Path(destination)
    else:
        target = default_output_path(source)
    if target.resolve() == source.resolve() and not in_place:
        raise ValueError("refusing to overwrite source without in_place=True")
    atomic_write(target, cleaned)

    post_warnings = list(warnings)
    if any(entry.provenance_related for entry in removed):
        post_warnings.append("Provenance/authenticity metadata was removed.")
    return CleanResult(
        source=source,
        destination=target,
        mode=clean_mode,
        format=format_name,
        adapter=adapter_name,
        removed=tuple(removed),
        kept=tuple(kept),
        bytes_before=len(data),
        bytes_after=len(cleaned),
        warnings=tuple(post_warnings),
    )
