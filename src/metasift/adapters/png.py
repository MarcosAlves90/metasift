from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ResourceTracker
from . import exif as exif_codec
from .common import make_entry

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_METADATA_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME", b"caBX"}


@dataclass(frozen=True, slots=True)
class PngChunk:
    chunk_type: bytes
    payload: bytes
    raw: bytes


def matches(data: bytes, _path_suffix: str = "") -> bool:
    return data.startswith(PNG_SIGNATURE)


def _bounded_decompress(data: bytes, limit: int) -> bytes:
    decoder = zlib.decompressobj()
    try:
        out = decoder.decompress(data, limit + 1)
    except zlib.error as exc:
        raise ValueError("invalid compressed PNG metadata") from exc
    if len(out) > limit or decoder.unconsumed_tail:
        raise ValueError(f"compressed PNG metadata exceeds safety limit ({limit:,} bytes)")
    remaining = limit + 1 - len(out)
    try:
        out += decoder.flush(remaining)
    except zlib.error as exc:
        raise ValueError("invalid compressed PNG metadata") from exc
    if len(out) > limit or not decoder.eof:
        raise ValueError(f"compressed PNG metadata exceeds safety limit ({limit:,} bytes)")
    return out


def _text_payload(chunk_type: bytes, payload: bytes, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[str, bytes]:
    if chunk_type == b"tEXt":
        key, _, value = payload.partition(b"\x00")
        return key.decode("latin-1", errors="replace"), value
    if chunk_type == b"zTXt":
        key, sep, rest = payload.partition(b"\x00")
        if not sep or len(rest) < 2:
            raise ValueError("invalid PNG zTXt chunk")
        if rest[0] != 0:
            raise ValueError("unsupported PNG zTXt compression method")
        value = _bounded_decompress(rest[1:], budget.max_metadata_entry_bytes)
        return key.decode("latin-1", errors="replace"), value
    if chunk_type == b"iTXt":
        key, sep, rest = payload.partition(b"\x00")
        if not sep or len(rest) < 2:
            raise ValueError("invalid PNG iTXt chunk")
        compression_flag, compression_method = rest[0], rest[1]
        if compression_flag not in {0, 1} or compression_method != 0:
            raise ValueError("unsupported PNG iTXt compression settings")
        rest = rest[2:]
        _language, sep, rest = rest.partition(b"\x00")
        if not sep:
            raise ValueError("invalid PNG iTXt language field")
        _translated, sep, text = rest.partition(b"\x00")
        if not sep:
            raise ValueError("invalid PNG iTXt translated keyword field")
        if compression_flag == 1:
            text = _bounded_decompress(text, budget.max_metadata_entry_bytes)
        return key.decode("latin-1", errors="replace"), text
    return chunk_type.decode("ascii", errors="replace"), payload


def _raw_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def parse(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[list[PngChunk], bytes]:
    if not matches(data):
        raise ValueError("not a PNG file")
    tracker = ResourceTracker(budget)
    chunks: list[PngChunk] = []
    offset = len(PNG_SIGNATURE)
    seen_iend = False
    trailing = b""
    while offset < len(data):
        tracker.chunk()
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG payload")
        chunk_type = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        raw = data[offset:end]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError(f"invalid PNG CRC for {chunk_type!r}")
        if chunk_type in _METADATA_CHUNKS:
            tracker.metadata(length, label=f"PNG {chunk_type.decode('ascii', errors='replace')}")
        chunks.append(PngChunk(chunk_type, payload, raw))
        offset = end
        if chunk_type == b"IEND":
            seen_iend = True
            trailing = data[offset:]
            break
    if not seen_iend:
        raise ValueError("PNG has no IEND chunk")
    return chunks, trailing


def _entries_for(chunk: PngChunk, budget: ResourceBudget) -> tuple[MetadataEntry, ...]:
    chunk_type, payload = chunk.chunk_type, chunk.payload
    if chunk_type == b"caBX":
        return (
            make_entry(
                key="C2PA",
                source="PNG caBX",
                size=len(payload),
                value="C2PA manifest store",
                namespace="c2pa",
                path="c2pa.manifest-store",
                signal="c2pa-manifest-store",
                force_provenance=True,
                removal_impact="provenance-loss",
            ),
        )
    if chunk_type == b"eXIf":
        return exif_codec.inspect(payload, source="PNG eXIf")
    if chunk_type == b"tIME":
        return (
            make_entry(
                key="Timestamp",
                source="PNG tIME",
                size=len(payload),
                value=payload.hex(),
                namespace="png",
                path="png.timestamp",
                force_privacy=True,
            ),
        )
    if chunk_type in {b"tEXt", b"zTXt", b"iTXt"}:
        key, value = _text_payload(chunk_type, payload, budget)
        return (
            make_entry(
                key=key or chunk_type.decode("ascii"),
                source=f"PNG {chunk_type.decode('ascii')}",
                size=len(payload),
                value=value,
                namespace="png-text",
                path=f"png.text.{key or chunk_type.decode('ascii')}",
            ),
        )
    return ()


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    chunks, trailing = parse(data, budget=budget)
    entries = [entry for chunk in chunks for entry in _entries_for(chunk, budget)]
    if trailing:
        entries.append(
            make_entry(
                key="TrailingData",
                source="PNG after IEND",
                size=len(trailing),
                value=trailing[:256],
                namespace="png",
                path="png.trailing-data",
                removal_impact="hidden-content-loss",
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
    chunks, trailing = parse(data, budget=budget)
    output = bytearray(PNG_SIGNATURE)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []

    for chunk in chunks:
        if chunk.chunk_type == b"eXIf":
            rewritten, rem, kep = exif_codec.clean(
                chunk.payload,
                mode,
                remove_keys,
                keep_keys,
                source="PNG eXIf",
            )
            removed.extend(rem)
            kept.extend(kep)
            if rewritten is not None:
                # PNG eXIf stores TIFF bytes without the Exif\0\0 prefix.
                payload = rewritten[6:] if rewritten.startswith(b"Exif\x00\x00") else rewritten
                output.extend(_raw_chunk(b"eXIf", payload))
            continue
        entries = _entries_for(chunk, budget)
        if not entries:
            output.extend(chunk.raw)
            continue
        entry = entries[0]
        if should_remove(entry, mode, remove_keys, keep_keys):
            removed.append(entry)
        else:
            output.extend(chunk.raw)
            kept.append(entry)

    if trailing:
        entry = make_entry(
            key="TrailingData",
            source="PNG after IEND",
            size=len(trailing),
            value=trailing[:256],
            namespace="png",
            path="png.trailing-data",
            removal_impact="hidden-content-loss",
        )
        if should_remove(entry, mode, remove_keys, keep_keys):
            removed.append(entry)
        else:
            output.extend(trailing)
            kept.append(entry)
    return bytes(output), tuple(removed), tuple(kept)
