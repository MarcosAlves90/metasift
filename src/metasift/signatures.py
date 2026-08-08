from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Confidence, EvidenceCategory

AI_DIRECT_PATTERNS = (
    "openai",
    "chatgpt",
    "dall-e",
    "dalle",
    "stable diffusion",
    "automatic1111",
    "a1111",
    "comfyui",
    "novelai",
    "midjourney",
    "invokeai",
    "fooocus",
    "adobe firefly",
    "generative fill",
)

AI_WORKFLOW_KEYS = {
    "parameters",
    "prompt",
    "negative prompt",
    "workflow",
    "seed",
    "sampler",
    "cfg scale",
    "steps",
    "model",
    "model hash",
}

PRIVACY_PATTERNS = (
    "gps",
    "latitude",
    "longitude",
    "location",
    "author",
    "creator",
    "artist",
    "owner",
    "email",
    "serial number",
    "serialnumber",
    "device serial",
    "camera serial",
    "bodyserialnumber",
    "lensserialnumber",
    "document id",
    "instance id",
    "original document id",
    "lastmodifiedby",
)

PROVENANCE_PATTERNS = (
    "c2pa",
    "content credential",
    "content credentials",
    "jumbf",
)

AI_DISCLOSURE_PATTERNS = (
    "c2pa.ai-disclosure",
    "trainedalgorithmicmedia",
    "trained algorithmic media",
    "digitalsourcetype",
)

TECHNICAL_PATTERNS = (
    "icc",
    "colorspace",
    "color space",
    "orientation",
    "gamma",
    "chromatic",
    "resolution",
    "width",
    "height",
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+")


@dataclass(frozen=True, slots=True)
class Classification:
    category: str
    signal: str | None
    confidence: str
    ai_related: bool
    privacy_related: bool
    provenance_related: bool
    preserve_recommended: bool = False
    rendering_required: bool = False


def safe_preview(data: bytes | str, limit: int = 160) -> str:
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = data
    text = _CONTROL.sub(" ", text).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def classify_evidence(key: str, value: bytes | str = "") -> Classification:
    key_l = key.casefold().strip()
    preview = safe_preview(value, 2048).casefold()
    haystack = f"{key_l} {preview}"

    provenance = any(token in haystack for token in PROVENANCE_PATTERNS)
    ai_disclosure = any(token in haystack for token in AI_DISCLOSURE_PATTERNS)
    direct_ai = any(token in haystack for token in AI_DIRECT_PATTERNS)
    workflow_ai = key_l in AI_WORKFLOW_KEYS
    privacy = any(token in haystack for token in PRIVACY_PATTERNS)
    technical = any(token in haystack for token in TECHNICAL_PATTERNS)

    if ai_disclosure:
        return Classification(
            category=EvidenceCategory.PROVENANCE.value,
            signal="structured-ai-disclosure",
            confidence=Confidence.CONFIRMED.value,
            ai_related=True,
            privacy_related=privacy,
            provenance_related=True,
        )
    if provenance:
        return Classification(
            category=EvidenceCategory.PROVENANCE.value,
            signal="c2pa-or-provenance",
            confidence=Confidence.CONFIRMED.value,
            ai_related=False,
            privacy_related=privacy,
            provenance_related=True,
        )
    if direct_ai:
        return Classification(
            category=EvidenceCategory.WORKFLOW.value,
            signal="known-ai-workflow",
            confidence=Confidence.PROBABLE.value,
            ai_related=True,
            privacy_related=privacy,
            provenance_related=False,
        )
    if workflow_ai:
        return Classification(
            category=EvidenceCategory.WORKFLOW.value,
            signal="generic-generation-parameter",
            confidence=Confidence.POSSIBLE.value,
            ai_related=True,
            privacy_related=privacy,
            provenance_related=False,
        )
    if privacy:
        return Classification(
            category=EvidenceCategory.PRIVACY.value,
            signal="privacy-metadata",
            confidence=Confidence.CONFIRMED.value,
            ai_related=False,
            privacy_related=True,
            provenance_related=False,
        )
    if technical:
        return Classification(
            category=EvidenceCategory.TECHNICAL.value,
            signal="rendering-or-technical-metadata",
            confidence=Confidence.CONFIRMED.value,
            ai_related=False,
            privacy_related=False,
            provenance_related=False,
            preserve_recommended=True,
            rendering_required=key_l in {"orientation"},
        )
    return Classification(
        category=EvidenceCategory.METADATA.value,
        signal=None,
        confidence=Confidence.POSSIBLE.value,
        ai_related=False,
        privacy_related=False,
        provenance_related=False,
    )


def classify(key: str, value: bytes | str = "") -> tuple[bool, bool, bool]:
    """Backward-compatible boolean classifier."""
    result = classify_evidence(key, value)
    return result.ai_related, result.privacy_related, result.provenance_related
