from __future__ import annotations

import struct

from metasift.models import CleanMode, MetadataEntry
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ResourceTracker
from .common import ParsedUnit, make_entry, split_units

_METADATA_CHUNKS = {b"C2PA", b"EXIF", b"XMP ", b"ID3 ", b"bext", b"iXML", b"cart", b"DISP"}


def matches(data: bytes, _path_suffix: str = "") -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] != b"WEBP"


def parse(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[bytes, list[ParsedUnit]]:
    if not matches(data):
        raise ValueError("not a RIFF file")
    declared = struct.unpack("<I", data[4:8])[0]
    if declared + 8 > len(data):
        raise ValueError("truncated RIFF file")
    form = data[8:12]
    tracker = ResourceTracker(budget)
    units: list[ParsedUnit] = []
    offset = 12
    limit = min(len(data), declared + 8)
    while offset + 8 <= limit:
        tracker.chunk()
        chunk_id = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        padded_end = payload_end + (size & 1)
        if padded_end > limit:
            raise ValueError(f"truncated RIFF chunk {chunk_id!r}")
        payload = data[payload_start:payload_end]
        raw = data[offset:padded_end]
        entry = None
        if chunk_id in _METADATA_CHUNKS or (chunk_id == b"LIST" and payload[:4] == b"INFO"):
            tracker.metadata(size, label=f"RIFF {chunk_id.decode('ascii', errors='replace')}")
        if chunk_id == b"C2PA":
            entry = make_entry(
                key="C2PA",
                category="provenance",
                source=f"RIFF/{form.decode('ascii', errors='replace')} C2PA",
                size=size,
                value=payload[:512],
                force_provenance=True,
            )
        elif chunk_id in _METADATA_CHUNKS:
            key = chunk_id.decode("ascii", errors="replace").strip()
            entry = make_entry(
                key=key,
                category="metadata",
                source=f"RIFF/{form.decode('ascii', errors='replace')} {key}",
                size=size,
                value=payload[:1024],
                force_privacy=chunk_id in {b"EXIF", b"bext", b"iXML", b"cart"},
            )
        elif chunk_id == b"LIST" and payload[:4] == b"INFO":
            entry = make_entry(
                key="RIFF INFO",
                category="metadata",
                source=f"RIFF/{form.decode('ascii', errors='replace')} LIST/INFO",
                size=size,
                value=payload[:1024],
                force_privacy=True,
            )
        units.append(ParsedUnit(raw, entry))
        offset = padded_end
    if offset < limit:
        units.append(ParsedUnit(data[offset:limit]))
    if limit < len(data):
        units.append(ParsedUnit(data[limit:]))
    return form, units


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    _form, units = parse(data, budget=budget)
    return tuple(unit.entry for unit in units if unit.entry is not None)


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    form, units = parse(data, budget=budget)
    output, removed, kept = split_units(units, mode, remove_keys, keep_keys)
    body = form + b"".join(unit.raw for unit in output)
    rebuilt = b"RIFF" + struct.pack("<I", len(body)) + body
    return rebuilt, removed, kept
