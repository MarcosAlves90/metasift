from __future__ import annotations

from dataclasses import dataclass

from metasift.models import CleanMode, Confidence, EvidenceCategory, MetadataEntry
from metasift.policy import should_remove
from metasift.signatures import classify_evidence, safe_preview


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    raw: bytes
    entry: MetadataEntry | None = None


def make_entry(
    *,
    key: str,
    category: str | None = None,
    source: str,
    size: int,
    value: bytes | str = "",
    namespace: str | None = None,
    path: str | None = None,
    signal: str | None = None,
    confidence: str | None = None,
    removable: bool = True,
    removal_impact: str = "metadata-loss",
    preserve_recommended: bool | None = None,
    rendering_required: bool | None = None,
    force_ai: bool = False,
    force_privacy: bool = False,
    force_provenance: bool = False,
) -> MetadataEntry:
    classification = classify_evidence(key, value)
    ai = classification.ai_related or force_ai
    privacy = classification.privacy_related or force_privacy
    provenance = classification.provenance_related or force_provenance

    resolved_category = category or classification.category
    if force_provenance:
        resolved_category = EvidenceCategory.PROVENANCE.value
    elif force_privacy and resolved_category in {None, EvidenceCategory.METADATA.value, "metadata"}:
        resolved_category = EvidenceCategory.PRIVACY.value
    elif force_ai and not force_provenance:
        resolved_category = EvidenceCategory.WORKFLOW.value

    resolved_confidence = confidence or classification.confidence
    if force_ai or force_privacy or force_provenance:
        resolved_confidence = Confidence.CONFIRMED.value

    return MetadataEntry(
        key=key,
        category=resolved_category or EvidenceCategory.METADATA.value,
        source=source,
        size=size,
        value_preview=safe_preview(value) if value else None,
        namespace=namespace,
        path=path,
        signal=signal or classification.signal,
        confidence=resolved_confidence,
        removable=removable,
        removal_impact=removal_impact,
        preserve_recommended=(
            classification.preserve_recommended if preserve_recommended is None else preserve_recommended
        ),
        rendering_required=(
            classification.rendering_required if rendering_required is None else rendering_required
        ),
        ai_related=ai,
        privacy_related=privacy,
        provenance_related=provenance,
    )


def split_units(
    units: list[ParsedUnit],
    mode: CleanMode,
    remove_keys: tuple[str, ...],
    keep_keys: tuple[str, ...],
) -> tuple[list[ParsedUnit], tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    output: list[ParsedUnit] = []
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for unit in units:
        if unit.entry is None:
            output.append(unit)
            continue
        if should_remove(unit.entry, mode, remove_keys, keep_keys):
            removed.append(unit.entry)
        else:
            output.append(unit)
            kept.append(unit.entry)
    return output, tuple(removed), tuple(kept)
