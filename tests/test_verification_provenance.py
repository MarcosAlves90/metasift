from __future__ import annotations

import json
from pathlib import Path

import pytest

from metasift.adapters import xmp
from metasift.models import CleanMode
from metasift.provenance import inspect_provenance
from metasift.verification import verify_file


def test_provenance_reports_structural_signal_without_optional_backend(sample_files):
    report = inspect_provenance(sample_files["png"])
    assert {entry.key for entry in report.structural_signals} == {"C2PA"}
    assert report.c2pa_available is False
    assert report.c2pa_status == "backend-unavailable"
    payload = report.to_dict()
    assert payload["c2pa"]["backend_available"] is False


def test_verification_runs_pillow_and_mutagen_checks(sample_files):
    image = verify_file(sample_files["png"], mode=CleanMode.AI, independent=True)
    assert image.supported and image.clean is False
    backends = {check["backend"]: check for check in image.independent_checks}
    assert backends["ExifTool"]["status"] in {"unavailable", "ok"}
    assert backends["Pillow"]["status"] == "ok"
    assert "parameters" in {entry.key for entry in image.remaining}

    audio = verify_file(sample_files["mp3"], mode=CleanMode.PRIVACY, independent=True)
    audio_checks = {check["backend"]: check for check in audio.independent_checks}
    assert audio_checks["Mutagen"]["status"] == "ok"
    assert audio.clean is False


def test_xmp_structured_ai_disclosure_and_selective_cleaning():
    payload = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:iptc="urn:iptc">'
        b'<iptc:DigitalSourceType>trainedAlgorithmicMedia</iptc:DigitalSourceType>'
        b'<creator>Alice</creator><prompt>owl</prompt></x:xmpmeta>'
    )
    entries = xmp.inspect(payload, source="test")
    digital = next(entry for entry in entries if entry.key == "DigitalSourceType")
    assert digital.provenance_related and digital.ai_related and digital.confidence == "confirmed"

    cleaned, removed, kept = xmp.clean(payload, CleanMode.SHARE_SAFE, source="test")
    assert cleaned is not None
    assert {entry.key for entry in removed} == {"creator", "prompt"}
    assert {entry.key for entry in kept} == {"DigitalSourceType"}
    assert b"trainedAlgorithmicMedia" in cleaned and b"Alice" not in cleaned


def test_xmp_rejects_dtd_and_invalid_xml():
    with pytest.raises(ValueError, match="DTD"):
        xmp.inspect(b'<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>', source="test")
    with pytest.raises(ValueError, match="invalid XMP"):
        xmp.inspect(b"<broken>", source="test")
