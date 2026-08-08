from __future__ import annotations

from typing import Any

_CAPABILITIES: dict[str, dict[str, Any]] = {
    "jpeg": {
        "inspect": "field-level",
        "sanitize": "lossless-container",
        "selective_exif": True,
        "selective_xmp": True,
        "c2pa": "structural-remove; optional-official-verify",
        "fidelity": "entropy-coded scan preserved for container cleaning",
    },
    "png": {
        "inspect": "field-level-exif; chunk-level-text",
        "sanitize": "lossless-container",
        "selective_exif": True,
        "c2pa": "caBX remove; optional-official-verify",
        "fidelity": "IDAT chunks preserved for container cleaning",
    },
    "webp": {
        "inspect": "field-level-exif; chunk-level-xmp/icc",
        "sanitize": "lossless-container",
        "selective_exif": True,
        "vp8x_consistency": True,
        "c2pa": "C2PA chunk remove; optional-official-verify",
        "fidelity": "VP8/VP8L/ANMF media chunks preserved",
    },
    "riff": {
        "inspect": "chunk-level",
        "sanitize": "lossless-container",
        "c2pa": "C2PA chunk remove",
    },
    "gif": {
        "inspect": "extension-level",
        "sanitize": "lossless-container",
        "c2pa": "C2PA_GIF application extension remove",
    },
    "ooxml": {
        "inspect": "document-properties + hidden-content inventory",
        "sanitize": "core/custom-properties only",
        "hidden_content_removal": False,
        "archive_budgets": True,
    },
    "mp3": {
        "inspect": "ID3 frame-level when mutagen is available",
        "sanitize": "ID3 frame-level when mutagen is available; container fallback",
        "audio_frames_preserved": True,
    },
    "generic": {
        "inspect": "unsupported",
        "sanitize": False,
    },
}


def capabilities_for(adapter: str) -> dict[str, Any]:
    return dict(_CAPABILITIES.get(adapter, _CAPABILITIES["generic"]))


def capability_matrix() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _CAPABILITIES.items()}
