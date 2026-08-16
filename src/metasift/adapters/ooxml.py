from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from copy import copy
from pathlib import PurePosixPath

from metasift.models import CleanMode, EvidenceCategory, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ensure_xml_size
from .common import make_entry

_CORE = "docProps/core.xml"
_CUSTOM = "docProps/custom.xml"
_OOXML_SUFFIXES = {
    ".docx", ".docm", ".dotx", ".dotm",
    ".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm", ".xlam",
    ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm", ".ppam",
}


def matches(data: bytes, path_suffix: str = "") -> bool:
    if path_suffix.casefold() not in _OOXML_SUFFIXES:
        return False
    return data.startswith(b"PK\x03\x04")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _validate_archive(archive: zipfile.ZipFile, budget: ResourceBudget) -> None:
    infos = archive.infolist()
    if len(infos) > budget.max_zip_entries:
        raise ValueError(f"OOXML archive exceeds entry limit ({budget.max_zip_entries:,})")
    total = 0
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe OOXML member path: {info.filename}")
        if info.flag_bits & 0x1:
            raise ValueError(f"encrypted OOXML member is not supported: {info.filename}")
        if info.file_size > budget.max_zip_entry_bytes:
            raise ValueError(
                f"OOXML member exceeds per-entry limit ({budget.max_zip_entry_bytes:,} bytes): {info.filename}"
            )
        total += info.file_size
        if total > budget.max_zip_uncompressed_bytes:
            raise ValueError(
                f"OOXML archive exceeds uncompressed-size limit ({budget.max_zip_uncompressed_bytes:,} bytes)"
            )
        if info.file_size and info.file_size > max(1, info.compress_size) * budget.max_zip_compression_ratio:
            raise ValueError(
                f"OOXML member exceeds compression-ratio limit ({budget.max_zip_compression_ratio}:1): {info.filename}"
            )


def _read_xml(archive: zipfile.ZipFile, name: str, budget: ResourceBudget) -> bytes:
    info = archive.getinfo(name)
    ensure_xml_size(info.file_size, budget)
    raw = archive.read(name)
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError(f"OOXML XML part contains prohibited DTD/entity declarations: {name}")
    return raw


def _property_entries(archive: zipfile.ZipFile, budget: ResourceBudget) -> list[MetadataEntry]:
    entries: list[MetadataEntry] = []
    names = set(archive.namelist())
    if _CORE in names:
        root = ET.fromstring(_read_xml(archive, _CORE, budget))
        for child in root:
            value = "" if child.text is None else child.text
            key = _local(child.tag)
            entries.append(
                make_entry(
                    key=key,
                    source="OOXML core properties",
                    size=len(value.encode("utf-8")),
                    value=value,
                    namespace="ooxml-core",
                    path=f"ooxml.core.{key}",
                    force_privacy=key.casefold() in {"creator", "lastmodifiedby", "created", "modified", "identifier"},
                )
            )
    if _CUSTOM in names:
        root = ET.fromstring(_read_xml(archive, _CUSTOM, budget))
        for prop in root:
            key = prop.attrib.get("name", "custom-property")
            value = "".join(prop.itertext())
            entries.append(
                make_entry(
                    key=key,
                    source="OOXML custom properties",
                    size=len(value.encode("utf-8")),
                    value=value,
                    namespace="ooxml-custom",
                    path=f"ooxml.custom.{key}",
                )
            )
    return entries


def _hidden_content_entries(archive: zipfile.ZipFile) -> list[MetadataEntry]:
    names = archive.namelist()
    entries: list[MetadataEntry] = []

    groups = {
        "Comments": [name for name in names if name.endswith("comments.xml")],
        "CustomXML": [name for name in names if name.startswith("customXml/") and not name.endswith(".rels")],
        "EmbeddedObjects": [name for name in names if "/embeddings/" in name],
        "Macros": [name for name in names if name.casefold().endswith("vbaproject.bin")],
        "SpeakerNotes": [name for name in names if name.startswith("ppt/notesSlides/") and name.endswith(".xml")],
    }
    for key, members in groups.items():
        if not members:
            continue
        entries.append(
            make_entry(
                key=key,
                category=EvidenceCategory.HIDDEN_CONTENT.value,
                source="OOXML package structure",
                size=sum(archive.getinfo(name).file_size for name in members),
                value=f"{len(members)} package member(s)",
                namespace="ooxml-hidden",
                path=f"ooxml.hidden.{key.casefold()}",
                removable=False,
                removal_impact="document-semantic-change",
            )
        )
    return entries


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        _validate_archive(archive, budget)
        return tuple(_property_entries(archive, budget) + _hidden_content_entries(archive))


def _clean_xml(
    raw: bytes,
    source: str,
    mode: CleanMode,
    remove_keys: tuple[str, ...],
    keep_keys: tuple[str, ...],
    custom: bool,
) -> tuple[bytes, list[MetadataEntry], list[MetadataEntry]]:
    root = ET.fromstring(raw)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for child in tuple(root):
        if custom:
            key = child.attrib.get("name", "custom-property")
            value = "".join(child.itertext())
            namespace = "ooxml-custom"
            path = f"ooxml.custom.{key}"
        else:
            key = _local(child.tag)
            value = "" if child.text is None else child.text
            namespace = "ooxml-core"
            path = f"ooxml.core.{key}"
        entry = make_entry(
            key=key,
            source=source,
            size=len(value.encode("utf-8")),
            value=value,
            namespace=namespace,
            path=path,
            force_privacy=(not custom and key.casefold() in {"creator", "lastmodifiedby", "created", "modified", "identifier"}),
        )
        if should_remove(entry, mode, remove_keys, keep_keys):
            root.remove(child)
            removed.append(entry)
        else:
            kept.append(entry)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), removed, kept


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    source = io.BytesIO(data)
    output = io.BytesIO()
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    with zipfile.ZipFile(source, "r") as zin:
        _validate_archive(zin, budget)
        with zipfile.ZipFile(output, "w") as zout:
            for info in zin.infolist():
                payload = zin.read(info.filename)
                if info.filename == _CORE:
                    ensure_xml_size(len(payload), budget)
                    payload, rem, kep = _clean_xml(
                        payload, "OOXML core properties", mode, remove_keys, keep_keys, custom=False
                    )
                    removed.extend(rem)
                    kept.extend(kep)
                elif info.filename == _CUSTOM:
                    ensure_xml_size(len(payload), budget)
                    payload, rem, kep = _clean_xml(
                        payload, "OOXML custom properties", mode, remove_keys, keep_keys, custom=True
                    )
                    removed.extend(rem)
                    kept.extend(kep)
                new_info = copy(info)
                if mode in {CleanMode.FULL, CleanMode.METADATA_MAX}:
                    new_info.date_time = (1980, 1, 1, 0, 0, 0)
                zout.writestr(new_info, payload)
    return output.getvalue(), tuple(removed), tuple(kept)
