from __future__ import annotations

from collections.abc import Iterable

from .models import CleanMode, MetadataEntry


def _selectors(values: Iterable[str]) -> set[str]:
    return {item.casefold().strip() for item in values if item.strip()}


def _matches(entry: MetadataEntry, selectors: set[str]) -> bool:
    candidates = {entry.key.casefold(), entry.selector.casefold()}
    if entry.namespace:
        candidates.add(f"{entry.namespace}.{entry.key}".casefold())
    return bool(candidates & selectors)


def should_remove(
    entry: MetadataEntry,
    mode: CleanMode,
    remove_keys: Iterable[str] = (),
    keep_keys: Iterable[str] = (),
) -> bool:
    remove = _selectors(remove_keys)
    keep = _selectors(keep_keys)

    if _matches(entry, keep):
        return False
    if not entry.removable:
        return False
    if _matches(entry, remove):
        return True
    if mode is CleanMode.CUSTOM:
        return False
    if mode is CleanMode.AI:
        return entry.ai_related or entry.provenance_related
    if mode is CleanMode.WORKFLOW:
        return entry.ai_related and not entry.provenance_related
    if mode is CleanMode.PROVENANCE:
        return entry.provenance_related
    if mode is CleanMode.PRIVACY:
        return entry.privacy_related
    if mode is CleanMode.SHARE_SAFE:
        return (entry.privacy_related or (entry.ai_related and not entry.provenance_related)) and not entry.rendering_required
    if mode in {CleanMode.METADATA_MAX, CleanMode.FULL}:
        return not entry.rendering_required
    return False
