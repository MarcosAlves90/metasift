from __future__ import annotations

import xml.etree.ElementTree as ET

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ensure_xml_size
from .common import make_entry


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None


def _parse(payload: bytes, budget: ResourceBudget) -> ET.Element:
    ensure_xml_size(len(payload), budget)
    head = payload[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError("XMP with DTD/entity declarations is not accepted")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("invalid XMP XML") from exc


def _entry(key: str, value: str, source: str, path: str, namespace: str | None) -> MetadataEntry:
    lower = key.casefold()
    force_privacy = lower in {
        "creator",
        "author",
        "artist",
        "creatortool",
        "documentid",
        "instanceid",
        "originaldocumentid",
        "metadatadate",
    }
    force_ai = False
    force_provenance = lower in {"digitalsourcetype", "ai-disclosure", "c2pa.ai-disclosure"}
    signal = None
    if lower == "digitalsourcetype" and "trainedalgorithmicmedia" in value.casefold():
        signal = "iptc-trained-algorithmic-media"
        force_ai = True
        force_provenance = True
    elif "ai-disclosure" in lower:
        signal = "c2pa-ai-disclosure"
        force_ai = True
        force_provenance = True
    return make_entry(
        key=key,
        source=source,
        size=len(value.encode("utf-8", errors="replace")),
        value=value,
        namespace=namespace or "xmp",
        path=path,
        signal=signal,
        force_ai=force_ai,
        force_privacy=force_privacy,
        force_provenance=force_provenance,
    )


def _walk(root: ET.Element, source: str) -> list[tuple[ET.Element, str | None, ET.Element | None, MetadataEntry]]:
    rows: list[tuple[ET.Element, str | None, ET.Element | None, MetadataEntry]] = []

    def visit(element: ET.Element, parent: ET.Element | None, parents: tuple[str, ...]) -> None:
        local = _local(element.tag)
        current = parents + (local,)
        text = (element.text or "").strip()
        if text:
            rows.append(
                (
                    element,
                    None,
                    parent,
                    _entry(local, text, source, "xmp." + ".".join(current), _namespace(element.tag)),
                )
            )
        for attr, value in list(element.attrib.items()):
            attr_local = _local(attr)
            rows.append(
                (
                    element,
                    attr,
                    parent,
                    _entry(
                        attr_local,
                        value,
                        source,
                        "xmp." + ".".join(current + ("@" + attr_local,)),
                        _namespace(attr),
                    ),
                )
            )
        for child in list(element):
            visit(child, element, current)

    visit(root, None, ())
    return rows


def inspect(payload: bytes, *, source: str, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    root = _parse(payload, budget)
    return tuple(row[3] for row in _walk(root, source))


def clean(
    payload: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    source: str,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes | None, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    root = _parse(payload, budget)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for element, attr, parent, entry in _walk(root, source):
        if should_remove(entry, mode, remove_keys, keep_keys):
            if attr is None:
                element.text = None
            else:
                element.attrib.pop(attr, None)
            removed.append(entry)
        else:
            kept.append(entry)
    if not kept:
        return None, tuple(removed), ()
    return ET.tostring(root, encoding="utf-8", xml_declaration=False), tuple(removed), tuple(kept)
