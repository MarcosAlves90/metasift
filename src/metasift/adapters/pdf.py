from __future__ import annotations

import io
from typing import Any

from pypdf import PdfReader, PdfWriter

from metasift.models import CleanMode, EvidenceCategory, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget
from . import xmp as xmp_codec
from .common import make_entry


def matches(data: bytes, path_suffix: str = "") -> bool:
    return data.lstrip().startswith(b"%PDF-") or path_suffix.casefold() == ".pdf"


def _reader(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
    except Exception as exc:
        raise ValueError("invalid PDF document") from exc
    if reader.is_encrypted:
        raise ValueError("encrypted PDF documents are not supported for metadata sanitization")
    return reader


def _signature_fields(reader: PdfReader) -> list[tuple[str, Any]]:
    try:
        fields = reader.get_fields() or {}
    except Exception:
        return []
    result = []
    for name, field in fields.items():
        try:
            field_type = field.get("/FT")
            value = field.get("/V")
        except AttributeError:
            continue
        if str(field_type) == "/Sig" or value is not None and str(getattr(value, "get", lambda *_: None)("/Type")) == "/Sig":
            result.append((str(name), field))
    return result


def _xmp_bytes(reader: PdfReader) -> bytes | None:
    try:
        root = reader.trailer["/Root"]
        metadata = root.get("/Metadata")
        if metadata is None:
            return None
        stream = metadata.get_object()
        raw = stream.get_data()
        return bytes(raw)
    except Exception as exc:
        raise ValueError("invalid PDF XMP metadata stream") from exc


def _info_rows(reader: PdfReader) -> list[tuple[str, MetadataEntry]]:
    metadata = reader.metadata
    if metadata is None:
        return []
    rows = []
    for raw_key, raw_value in metadata.items():
        key = str(raw_key).lstrip("/")
        value = "" if raw_value is None else str(raw_value)
        rows.append(
            (
                str(raw_key),
                make_entry(
                    key=key,
                    source="PDF Info dictionary",
                    size=len(value.encode("utf-8", errors="replace")),
                    value=value,
                    namespace="pdf-info",
                    path=f"pdf.info.{key}",
                    force_privacy=key.casefold() in {"author", "creator", "lastmodifiedby"},
                ),
            )
        )
    return rows


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    reader = _reader(data)
    entries = [entry for _key, entry in _info_rows(reader)]
    xmp = _xmp_bytes(reader)
    if xmp is not None:
        entries.extend(xmp_codec.inspect(xmp, source="PDF XMP", budget=budget))
    for name, _field in _signature_fields(reader):
        entries.append(
            make_entry(
                key="DigitalSignature",
                category=EvidenceCategory.PROVENANCE.value,
                source="PDF signature field",
                size=0,
                value=name,
                namespace="pdf-signature",
                path=f"pdf.signature.{name}",
                signal="digital-signature",
                removable=False,
                removal_impact="signature-invalidation",
                preserve_recommended=True,
                force_provenance=True,
            )
        )
    return tuple(entries)


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    reader = _reader(data)
    if _signature_fields(reader):
        raise ValueError("digitally signed PDF documents are not rewritten because sanitization would invalidate signatures")

    info_rows = _info_rows(reader)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    retained_info: dict[str, str] = {}
    for raw_key, entry in info_rows:
        if should_remove(entry, mode, remove_keys, keep_keys):
            removed.append(entry)
        else:
            raw_value = reader.metadata.get(raw_key) if reader.metadata is not None else None
            if raw_value is not None:
                retained_info[raw_key] = str(raw_value)
            kept.append(entry)

    xmp = _xmp_bytes(reader)
    rewritten_xmp = xmp
    if xmp is not None:
        rewritten_xmp, rem, kep = xmp_codec.clean(
            xmp,
            mode,
            remove_keys,
            keep_keys,
            source="PDF XMP",
            budget=budget,
        )
        removed.extend(rem)
        kept.extend(kep)

    if not removed:
        return data, (), tuple(kept)

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.metadata = retained_info or None
    writer.xmp_metadata = rewritten_xmp  # pypdf accepts raw XMP bytes through this setter.
    output = io.BytesIO()
    try:
        writer.write(output)
    except Exception as exc:
        raise ValueError("PDF metadata rewrite failed") from exc
    return output.getvalue(), tuple(removed), tuple(kept)
