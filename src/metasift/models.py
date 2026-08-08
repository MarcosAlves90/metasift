from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class CleanMode(str, Enum):
    # Legacy compatibility: AI means workflow + provenance.
    AI = "ai"
    PRIVACY = "privacy"
    WORKFLOW = "workflow"
    PROVENANCE = "provenance"
    SHARE_SAFE = "share-safe"
    METADATA_MAX = "metadata-max"
    # `full` is retained as a legacy spelling for maximum metadata removal.
    FULL = "full"
    CUSTOM = "custom"


class EvidenceCategory(str, Enum):
    PRIVACY = "privacy"
    WORKFLOW = "workflow"
    PROVENANCE = "provenance"
    TECHNICAL = "technical"
    HIDDEN_CONTENT = "hidden-content"
    METADATA = "metadata"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MetadataEntry:
    key: str
    category: str
    source: str
    size: int
    value_preview: str | None = None
    namespace: str | None = None
    path: str | None = None
    signal: str | None = None
    confidence: str = Confidence.POSSIBLE.value
    removable: bool = True
    removal_impact: str = "metadata-loss"
    preserve_recommended: bool = False
    rendering_required: bool = False
    # Compatibility flags. New code should prefer category/signal/confidence.
    ai_related: bool = False
    privacy_related: bool = False
    provenance_related: bool = False

    @property
    def selector(self) -> str:
        return self.path or self.key

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selector"] = self.selector
        return payload


@dataclass(frozen=True, slots=True)
class InspectionReport:
    path: Path
    format: str
    adapter: str
    size: int
    metadata: tuple[MetadataEntry, ...]
    warnings: tuple[str, ...] = ()
    capabilities: dict[str, Any] | None = None

    @property
    def ai_related_count(self) -> int:
        return sum(item.ai_related or item.provenance_related for item in self.metadata)

    @property
    def privacy_related_count(self) -> int:
        return sum(item.privacy_related for item in self.metadata)

    @property
    def provenance_count(self) -> int:
        return sum(item.provenance_related for item in self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "path": str(self.path),
            "format": self.format,
            "adapter": self.adapter,
            "size": self.size,
            "metadata": [item.to_dict() for item in self.metadata],
            "ai_related_count": self.ai_related_count,
            "privacy_related_count": self.privacy_related_count,
            "provenance_count": self.provenance_count,
            "capabilities": self.capabilities or {},
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class SanitizationPlan:
    source: Path
    mode: CleanMode
    format: str
    adapter: str
    remove: tuple[MetadataEntry, ...]
    preserve: tuple[MetadataEntry, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "source": str(self.source),
            "mode": self.mode.value,
            "format": self.format,
            "adapter": self.adapter,
            "remove": [item.to_dict() for item in self.remove],
            "preserve": [item.to_dict() for item in self.preserve],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class CleanResult:
    source: Path
    destination: Path
    mode: CleanMode
    format: str
    adapter: str
    removed: tuple[MetadataEntry, ...]
    kept: tuple[MetadataEntry, ...]
    bytes_before: int
    bytes_after: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "source": str(self.source),
            "destination": str(self.destination),
            "mode": self.mode.value,
            "format": self.format,
            "adapter": self.adapter,
            "removed": [item.to_dict() for item in self.removed],
            "kept": [item.to_dict() for item in self.kept],
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    path: Path
    mode: CleanMode
    supported: bool
    clean: bool | None
    remaining: tuple[MetadataEntry, ...]
    independent_checks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "path": str(self.path),
            "mode": self.mode.value,
            "supported": self.supported,
            "clean": self.clean,
            "remaining_target_metadata": [entry.to_dict() for entry in self.remaining],
            "independent_checks": list(self.independent_checks),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ProvenanceReport:
    path: Path
    structural_signals: tuple[MetadataEntry, ...]
    c2pa_available: bool
    c2pa_status: str
    c2pa_manifest: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "path": str(self.path),
            "structural_signals": [item.to_dict() for item in self.structural_signals],
            "c2pa": {
                "backend_available": self.c2pa_available,
                "status": self.c2pa_status,
                "manifest": self.c2pa_manifest,
            },
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ImageCleanResult:
    source: Path
    destination: Path
    input_format: str
    output_format: str
    width: int
    height: int
    quality: int | None
    jitter: int
    bytes_before: int
    bytes_after: int
    sha256_before: str
    sha256_after: str
    alpha_preserved: bool
    icc_preserved: bool
    frames_preserved: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "source": str(self.source),
            "destination": str(self.destination),
            "input_format": self.input_format,
            "output_format": self.output_format,
            "width": self.width,
            "height": self.height,
            "quality": self.quality,
            "jitter": self.jitter,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "sha256_before": self.sha256_before,
            "sha256_after": self.sha256_after,
            "alpha_preserved": self.alpha_preserved,
            "icc_preserved": self.icc_preserved,
            "frames_preserved": self.frames_preserved,
            "warnings": list(self.warnings),
        }
