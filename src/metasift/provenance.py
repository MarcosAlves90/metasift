from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from .engine import inspect_file
from .models import ProvenanceReport
from .resource_limits import DEFAULT_BUDGET, ResourceBudget


def _load_c2pa(path: Path) -> tuple[bool, str, dict[str, Any] | None, list[str]]:
    if importlib.util.find_spec("c2pa") is None:
        return False, "backend-unavailable", None, ["Install the optional 'c2pa' extra to validate Content Credentials cryptographically."]
    try:
        import c2pa  # type: ignore

        reader = c2pa.Reader(str(path))
        store = json.loads(reader.json())
        active = store.get("active_manifest")
        manifests = store.get("manifests", {})
        active_manifest = manifests.get(active) if active else None
        payload = {
            "active_manifest": active,
            "active_manifest_data": active_manifest,
            "validation_status": store.get("validation_status"),
        }
        return True, "validated", payload, []
    except Exception as exc:  # SDK exposes native exception classes that vary by release.
        return True, "not-present-or-invalid", None, [f"c2pa-python could not validate this asset: {exc}"]


def inspect_provenance(path: str | Path, *, budget: ResourceBudget = DEFAULT_BUDGET) -> ProvenanceReport:
    source = Path(path)
    inspection = inspect_file(source, budget=budget)
    structural = tuple(
        entry
        for entry in inspection.metadata
        if entry.provenance_related or entry.signal in {"structured-ai-disclosure", "iptc-trained-algorithmic-media", "c2pa-ai-disclosure"}
    )
    available, status, manifest, warnings = _load_c2pa(source)
    if structural and status == "not-present-or-invalid":
        warnings.append("Structural provenance data is present but the optional C2PA validator did not produce a valid manifest.")
    return ProvenanceReport(
        path=source,
        structural_signals=structural,
        c2pa_available=available,
        c2pa_status=status,
        c2pa_manifest=manifest,
        warnings=tuple(inspection.warnings) + tuple(warnings),
    )
