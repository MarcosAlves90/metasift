from __future__ import annotations

from metasift.models import CleanMode, MetadataEntry
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ResourceTracker
from .common import ParsedUnit, make_entry, split_units


def matches(data: bytes, _path_suffix: str = "") -> bool:
    return data.startswith((b"GIF87a", b"GIF89a"))


def _subblocks_end(data: bytes, offset: int) -> int:
    while True:
        if offset >= len(data):
            raise ValueError("truncated GIF sub-block")
        size = data[offset]
        offset += 1
        if size == 0:
            return offset
        offset += size
        if offset > len(data):
            raise ValueError("truncated GIF sub-block payload")


def _subblocks_payload(data: bytes, offset: int) -> bytes:
    out = bytearray()
    while True:
        size = data[offset]
        offset += 1
        if size == 0:
            return bytes(out)
        out.extend(data[offset : offset + size])
        offset += size


def parse(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> list[ParsedUnit]:
    if not matches(data):
        raise ValueError("not a GIF file")
    if len(data) < 13:
        raise ValueError("truncated GIF header")
    packed = data[10]
    gct_size = 3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0
    header_end = 13 + gct_size
    if header_end > len(data):
        raise ValueError("truncated GIF color table")
    tracker = ResourceTracker(budget)
    units = [ParsedUnit(data[:header_end])]
    offset = header_end
    while offset < len(data):
        tracker.chunk()
        start = offset
        introducer = data[offset]
        offset += 1
        if introducer == 0x3B:
            units.append(ParsedUnit(data[start:offset]))
            if offset < len(data):
                units.append(ParsedUnit(data[offset:]))
            break
        if introducer == 0x2C:  # image descriptor
            if offset + 9 > len(data):
                raise ValueError("truncated GIF image descriptor")
            image_packed = data[offset + 8]
            offset += 9
            if image_packed & 0x80:
                lct_size = 3 * (2 ** ((image_packed & 0x07) + 1))
                offset += lct_size
            if offset >= len(data):
                raise ValueError("truncated GIF image data")
            offset += 1  # LZW min code size
            offset = _subblocks_end(data, offset)
            units.append(ParsedUnit(data[start:offset]))
            continue
        if introducer != 0x21:
            raise ValueError(f"unknown GIF block introducer 0x{introducer:02x}")
        if offset >= len(data):
            raise ValueError("truncated GIF extension")
        label = data[offset]
        offset += 1
        entry = None
        if label == 0xFE:  # comment
            payload_start = offset
            offset = _subblocks_end(data, offset)
            payload = _subblocks_payload(data, payload_start)
            tracker.metadata(len(payload), label="GIF comment")
            entry = make_entry(key="Comment", category="text", source="GIF comment", size=len(payload), value=payload, namespace="gif", path="gif.comment")
        elif label == 0xFF:  # application extension
            if offset >= len(data):
                raise ValueError("truncated GIF application extension")
            block_size = data[offset]
            offset += 1
            if offset + block_size > len(data):
                raise ValueError("truncated GIF application identifier")
            app_id = data[offset : offset + block_size]
            offset += block_size
            payload_start = offset
            offset = _subblocks_end(data, offset)
            payload = _subblocks_payload(data, payload_start)
            app_key = app_id.decode("latin-1", errors="replace")
            tracker.metadata(len(payload), label=f"GIF application extension {app_key}")
            if app_id.startswith(b"C2PA_GIF"):
                entry = make_entry(
                    key="C2PA",
                    category="provenance",
                    source="GIF application extension",
                    size=len(payload),
                    value=payload[:512],
                    force_provenance=True,
                )
            elif app_id.startswith(b"XMP DataXMP"):
                entry = make_entry(key="XMP", category="metadata", source="GIF application extension", size=len(payload), value=payload[:1024])
            else:
                # Application extensions may affect rendering (for example NETSCAPE looping), so unknown ones are preserved.
                entry = None
        else:
            if offset >= len(data):
                raise ValueError("truncated GIF extension block")
            fixed_size = data[offset]
            offset += 1 + fixed_size
            if offset > len(data):
                raise ValueError("truncated GIF extension payload")
            offset = _subblocks_end(data, offset)
        units.append(ParsedUnit(data[start:offset], entry))
    return units


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    return tuple(unit.entry for unit in parse(data, budget=budget) if unit.entry is not None)


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    units = parse(data, budget=budget)
    output, removed, kept = split_units(units, mode, remove_keys, keep_keys)
    return b"".join(unit.raw for unit in output), removed, kept
