from __future__ import annotations

import struct
from dataclasses import dataclass

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ResourceTracker
from . import exif as exif_codec
from . import xmp as xmp_codec
from .common import make_entry

SOI = b"\xff\xd8"
SOS = 0xDA
EOI = 0xD9
_METADATA_MARKERS = {0xE1, 0xEB, 0xED, 0xFE}
_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
_C2PA_UUID = bytes.fromhex("6332706100110010800000aa00389b71")


@dataclass(slots=True)
class JpegUnit:
    raw: bytes
    marker: int | None = None
    payload: bytes | None = None
    c2pa_run: int | None = None


def matches(data: bytes, path_suffix: str = "") -> bool:
    return data.startswith(SOI) or path_suffix.casefold() in {".jpg", ".jpeg", ".jpe"}


def _looks_c2pa(payload: bytes) -> bool:
    probe = payload[:4096]
    return b"c2pa" in probe.lower() or _C2PA_UUID in probe


def parse(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> list[JpegUnit]:
    if not data.startswith(SOI):
        raise ValueError("not a JPEG file")
    tracker = ResourceTracker(budget)
    units = [JpegUnit(SOI)]
    offset = 2
    while offset < len(data):
        tracker.chunk()
        if data[offset] != 0xFF:
            raise ValueError("invalid JPEG marker alignment")
        marker_start = offset
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ValueError("truncated JPEG marker")
        marker = data[offset]
        offset += 1
        if marker == SOS:
            if offset + 2 > len(data):
                raise ValueError("truncated JPEG SOS")
            length = struct.unpack(">H", data[offset : offset + 2])[0]
            if length < 2 or offset + length > len(data):
                raise ValueError("invalid JPEG SOS length")
            units.append(JpegUnit(data[marker_start:], marker=SOS))
            break
        if marker == EOI:
            units.append(JpegUnit(data[marker_start:offset], marker=EOI))
            if offset < len(data):
                units.append(JpegUnit(data[offset:]))
            break
        if marker in {0x01} or 0xD0 <= marker <= 0xD7:
            units.append(JpegUnit(data[marker_start:offset], marker=marker))
            continue
        if offset + 2 > len(data):
            raise ValueError("truncated JPEG segment length")
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        if length < 2:
            raise ValueError("invalid JPEG segment length")
        end = offset + length
        if end > len(data):
            raise ValueError("truncated JPEG segment")
        payload = data[offset + 2 : end]
        if marker in _METADATA_MARKERS:
            tracker.metadata(len(payload), label=f"JPEG APP/COM marker 0x{marker:02x}")
        units.append(JpegUnit(data[marker_start:end], marker=marker, payload=payload))
        offset = end

    run_id = 0
    index = 0
    while index < len(units):
        if units[index].marker != 0xEB:
            index += 1
            continue
        end = index
        while end < len(units) and units[end].marker == 0xEB:
            end += 1
        run = units[index:end]
        if any(unit.payload is not None and _looks_c2pa(unit.payload) for unit in run):
            run_id += 1
            for unit in run:
                unit.c2pa_run = run_id
        index = end
    return units


def _segment(marker: int, payload: bytes) -> bytes:
    length = len(payload) + 2
    if length > 0xFFFF:
        raise ValueError("rewritten JPEG metadata segment exceeds JPEG APP size limit")
    return b"\xff" + bytes([marker]) + struct.pack(">H", length) + payload


def _xmp_payload(payload: bytes) -> tuple[bytes, bytes]:
    if payload.startswith(_XMP_HEADER):
        return _XMP_HEADER, payload[len(_XMP_HEADER) :]
    return b"", payload


def _simple_entry(unit: JpegUnit) -> MetadataEntry | None:
    marker = unit.marker
    payload = unit.payload or b""
    if marker == 0xED:
        return make_entry(
            key="IPTC/Photoshop",
            source="JPEG APP13",
            size=len(payload),
            value=payload[:1024],
            namespace="iptc",
            path="iptc.container",
            force_privacy=True,
        )
    if marker == 0xFE:
        return make_entry(
            key="Comment",
            source="JPEG COM",
            size=len(payload),
            value=payload,
            namespace="jpeg",
            path="jpeg.comment",
        )
    if marker == 0xE1:
        return make_entry(
            key="APP1",
            source="JPEG APP1",
            size=len(payload),
            value=payload[:256],
            namespace="jpeg",
            path="jpeg.app1",
        )
    if marker == 0xEB:
        return make_entry(
            key="APP11",
            source="JPEG APP11",
            size=len(payload),
            value=payload[:256],
            namespace="jpeg",
            path="jpeg.app11",
        )
    return None


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    units = parse(data, budget=budget)
    entries: list[MetadataEntry] = []
    seen_c2pa: set[int] = set()
    for unit in units:
        payload = unit.payload
        if unit.c2pa_run is not None:
            if unit.c2pa_run in seen_c2pa:
                continue
            seen_c2pa.add(unit.c2pa_run)
            total = sum(len(item.payload or b"") for item in units if item.c2pa_run == unit.c2pa_run)
            entries.append(
                make_entry(
                    key="C2PA",
                    source="JPEG APP11/JUMBF sequence",
                    size=total,
                    value="C2PA/JUMBF manifest store",
                    namespace="c2pa",
                    path="c2pa.manifest-store",
                    signal="c2pa-manifest-store",
                    force_provenance=True,
                    removal_impact="provenance-loss",
                )
            )
            continue
        if unit.marker == 0xE1 and payload is not None and payload.startswith(b"Exif\x00\x00"):
            fields = exif_codec.inspect(payload, source="JPEG APP1 EXIF")
            entries.extend(fields or (
                make_entry(key="EXIF", source="JPEG APP1", size=len(payload), namespace="exif", path="exif.container"),
            ))
            continue
        if unit.marker == 0xE1 and payload is not None and (
            payload.startswith(_XMP_HEADER) or b"<x:xmpmeta" in payload[:4096] or b"<rdf:RDF" in payload[:4096]
        ):
            _header, xml = _xmp_payload(payload)
            fields = xmp_codec.inspect(xml, source="JPEG APP1 XMP", budget=budget)
            entries.extend(fields or (
                make_entry(key="XMP", source="JPEG APP1", size=len(payload), namespace="xmp", path="xmp.container"),
            ))
            continue
        entry = _simple_entry(unit)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    units = parse(data, budget=budget)
    output: list[bytes] = []
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    handled_c2pa: set[int] = set()

    for unit in units:
        payload = unit.payload
        if unit.c2pa_run is not None:
            run = unit.c2pa_run
            if run in handled_c2pa:
                continue
            handled_c2pa.add(run)
            run_units = [item for item in units if item.c2pa_run == run]
            entry = make_entry(
                key="C2PA",
                source="JPEG APP11/JUMBF sequence",
                size=sum(len(item.payload or b"") for item in run_units),
                value="C2PA/JUMBF manifest store",
                namespace="c2pa",
                path="c2pa.manifest-store",
                signal="c2pa-manifest-store",
                force_provenance=True,
                removal_impact="provenance-loss",
            )
            if should_remove(entry, mode, remove_keys, keep_keys):
                removed.append(entry)
            else:
                output.extend(item.raw for item in run_units)
                kept.append(entry)
            continue

        if unit.marker == 0xE1 and payload is not None and payload.startswith(b"Exif\x00\x00"):
            rewritten, rem, kep = exif_codec.clean(
                payload,
                mode,
                remove_keys,
                keep_keys,
                source="JPEG APP1 EXIF",
            )
            removed.extend(rem)
            kept.extend(kep)
            if rewritten is not None:
                output.append(_segment(0xE1, rewritten))
            continue

        if unit.marker == 0xE1 and payload is not None and (
            payload.startswith(_XMP_HEADER) or b"<x:xmpmeta" in payload[:4096] or b"<rdf:RDF" in payload[:4096]
        ):
            header, xml = _xmp_payload(payload)
            rewritten, rem, kep = xmp_codec.clean(
                xml,
                mode,
                remove_keys,
                keep_keys,
                source="JPEG APP1 XMP",
                budget=budget,
            )
            removed.extend(rem)
            kept.extend(kep)
            if rewritten is not None:
                output.append(_segment(0xE1, header + rewritten))
            continue

        entry = _simple_entry(unit)
        if entry is None:
            output.append(unit.raw)
        elif should_remove(entry, mode, remove_keys, keep_keys):
            removed.append(entry)
        else:
            output.append(unit.raw)
            kept.append(entry)

    return b"".join(output), tuple(removed), tuple(kept)
