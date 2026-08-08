from __future__ import annotations

import struct
from dataclasses import dataclass

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ResourceTracker
from . import exif as exif_codec
from .common import make_entry


@dataclass(frozen=True, slots=True)
class WebPChunk:
    chunk_id: bytes
    payload: bytes
    raw: bytes


def matches(data: bytes, path_suffix: str = "") -> bool:
    return (
        len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    ) or path_suffix.casefold() == ".webp"


def _raw_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    raw = chunk_id + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        raw += b"\x00"
    return raw


def parse(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> list[WebPChunk]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP RIFF file")
    declared = struct.unpack("<I", data[4:8])[0]
    if declared + 8 != len(data):
        raise ValueError("WebP RIFF size does not match file size")
    tracker = ResourceTracker(budget)
    chunks: list[WebPChunk] = []
    offset = 12
    while offset < len(data):
        tracker.chunk()
        if offset + 8 > len(data):
            raise ValueError("truncated WebP chunk header")
        chunk_id = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        padded_end = payload_end + (size & 1)
        if padded_end > len(data):
            raise ValueError(f"truncated WebP chunk {chunk_id!r}")
        payload = data[payload_start:payload_end]
        raw = data[offset:padded_end]
        if chunk_id in {b"C2PA", b"EXIF", b"XMP ", b"ICCP"}:
            tracker.metadata(size, label=f"WebP {chunk_id.decode('ascii', errors='replace').strip()}")
        chunks.append(WebPChunk(chunk_id, payload, raw))
        offset = padded_end
    return chunks


def _entry_for(chunk: WebPChunk) -> MetadataEntry | None:
    if chunk.chunk_id == b"C2PA":
        return make_entry(
            key="C2PA",
            source="WebP C2PA",
            size=len(chunk.payload),
            value="C2PA manifest store",
            namespace="c2pa",
            path="c2pa.manifest-store",
            signal="c2pa-manifest-store",
            force_provenance=True,
            removal_impact="provenance-loss",
        )
    if chunk.chunk_id == b"XMP ":
        return make_entry(
            key="XMP",
            source="WebP XMP",
            size=len(chunk.payload),
            value=chunk.payload[:2048],
            namespace="xmp",
            path="xmp.container",
        )
    if chunk.chunk_id == b"ICCP":
        return make_entry(
            key="ICC",
            source="WebP ICCP",
            size=len(chunk.payload),
            namespace="icc",
            path="icc.profile",
            signal="color-profile",
            preserve_recommended=True,
            removal_impact="color-rendering-change",
        )
    return None


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    chunks = parse(data, budget=budget)
    entries: list[MetadataEntry] = []
    for chunk in chunks:
        if chunk.chunk_id == b"EXIF":
            entries.extend(exif_codec.inspect(chunk.payload, source="WebP EXIF"))
            continue
        entry = _entry_for(chunk)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _fix_vp8x(chunks: list[WebPChunk]) -> list[WebPChunk]:
    has_exif = any(chunk.chunk_id == b"EXIF" for chunk in chunks)
    has_xmp = any(chunk.chunk_id == b"XMP " for chunk in chunks)
    has_icc = any(chunk.chunk_id == b"ICCP" for chunk in chunks)
    result: list[WebPChunk] = []
    for chunk in chunks:
        if chunk.chunk_id != b"VP8X":
            result.append(chunk)
            continue
        if len(chunk.payload) != 10:
            raise ValueError("invalid WebP VP8X chunk length")
        flags = chunk.payload[0]
        flags = (flags | 0x08) if has_exif else (flags & ~0x08)
        flags = (flags | 0x04) if has_xmp else (flags & ~0x04)
        flags = (flags | 0x20) if has_icc else (flags & ~0x20)
        payload = bytes([flags]) + chunk.payload[1:]
        result.append(WebPChunk(b"VP8X", payload, _raw_chunk(b"VP8X", payload)))
    return result


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    chunks = parse(data, budget=budget)
    output: list[WebPChunk] = []
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []

    for chunk in chunks:
        if chunk.chunk_id == b"EXIF":
            rewritten, rem, kep = exif_codec.clean(
                chunk.payload,
                mode,
                remove_keys,
                keep_keys,
                source="WebP EXIF",
            )
            removed.extend(rem)
            kept.extend(kep)
            if rewritten is not None:
                output.append(WebPChunk(b"EXIF", rewritten, _raw_chunk(b"EXIF", rewritten)))
            continue
        entry = _entry_for(chunk)
        if entry is not None and should_remove(entry, mode, remove_keys, keep_keys):
            removed.append(entry)
        else:
            output.append(chunk)
            if entry is not None:
                kept.append(entry)

    output = _fix_vp8x(output)
    body = b"WEBP" + b"".join(chunk.raw for chunk in output)
    return b"RIFF" + struct.pack("<I", len(body)) + body, tuple(removed), tuple(kept)
