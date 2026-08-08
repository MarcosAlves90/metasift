from __future__ import annotations

import io

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ResourceTracker
from .common import make_entry

try:  # optional high-fidelity ID3 backend
    from mutagen.id3 import ID3, ID3NoHeaderError
except ImportError:  # pragma: no cover - exercised in environments without the extra
    ID3 = None
    ID3NoHeaderError = Exception


def matches(data: bytes, path_suffix: str = "") -> bool:
    return data.startswith(b"ID3") or path_suffix.casefold() == ".mp3"


def _syncsafe(value: bytes) -> int:
    if len(value) != 4 or any(byte & 0x80 for byte in value):
        raise ValueError("invalid ID3 syncsafe integer")
    return (value[0] << 21) | (value[1] << 14) | (value[2] << 7) | value[3]


def _regions(data: bytes, budget: ResourceBudget) -> tuple[int, int, bytes | None, bytes | None]:
    tracker = ResourceTracker(budget)
    start = 0
    end = len(data)
    id3v2 = None
    id3v1 = None
    if data.startswith(b"ID3"):
        if len(data) < 10:
            raise ValueError("truncated ID3v2 header")
        size = _syncsafe(data[6:10])
        footer = 10 if data[5] & 0x10 else 0
        tag_end = 10 + size + footer
        if tag_end > len(data):
            raise ValueError("truncated ID3v2 tag")
        tracker.metadata(tag_end, label="MP3 ID3v2")
        id3v2 = data[:tag_end]
        start = tag_end
    if len(data) - start >= 128 and data[-128:-125] == b"TAG":
        tracker.metadata(128, label="MP3 ID3v1")
        id3v1 = data[-128:]
        end = len(data) - 128
    return start, end, id3v2, id3v1


def _id3v2_entries(payload: bytes) -> tuple[MetadataEntry, ...]:
    if ID3 is None:
        return (
            make_entry(
                key="ID3v2",
                source="MP3 ID3v2",
                size=len(payload),
                value=payload[:2048],
                namespace="id3",
                path="id3.container",
            ),
        )
    try:
        tag = ID3(fileobj=io.BytesIO(payload))
    except ID3NoHeaderError:
        return ()
    entries: list[MetadataEntry] = []
    for frame_key, frame in tag.items():
        frame_id = getattr(frame, "FrameID", frame_key.split(":", 1)[0])
        text = str(frame)
        force_privacy = frame_id in {"TPE1", "TPE2", "TPE3", "TPE4", "TCOM", "TEXT", "COMM", "TOWN", "PRIV"}
        entries.append(
            make_entry(
                key=frame_key,
                source=f"MP3 ID3v2 {frame_id}",
                size=len(text.encode("utf-8", errors="replace")),
                value=text,
                namespace="id3",
                path=f"id3.{frame_key}",
                force_privacy=force_privacy,
            )
        )
    return tuple(entries)


def _id3v1_entries(payload: bytes) -> tuple[MetadataEntry, ...]:
    fields = (
        ("Title", payload[3:33]),
        ("Artist", payload[33:63]),
        ("Album", payload[63:93]),
        ("Year", payload[93:97]),
        ("Comment", payload[97:127]),
    )
    out: list[MetadataEntry] = []
    for key, raw in fields:
        value = raw.rstrip(b"\x00 ").decode("latin-1", errors="replace")
        if not value:
            continue
        out.append(
            make_entry(
                key=key,
                source="MP3 ID3v1",
                size=len(raw),
                value=value,
                namespace="id3v1",
                path=f"id3v1.{key}",
                force_privacy=key in {"Artist", "Comment"},
            )
        )
    return tuple(out)


def inspect(data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    _start, _end, id3v2, id3v1 = _regions(data, budget)
    entries: list[MetadataEntry] = []
    if id3v2 is not None:
        entries.extend(_id3v2_entries(id3v2))
    if id3v1 is not None:
        entries.extend(_id3v1_entries(id3v1))
    return tuple(entries)


def _serialize_id3v2(payload: bytes, mode: CleanMode, remove_keys: tuple[str, ...], keep_keys: tuple[str, ...]) -> tuple[bytes | None, list[MetadataEntry], list[MetadataEntry]]:
    entries = list(_id3v2_entries(payload))
    if ID3 is None:
        entry = entries[0]
        return (None, [entry], []) if should_remove(entry, mode, remove_keys, keep_keys) else (payload, [], [entry])

    tag = ID3(fileobj=io.BytesIO(payload))
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for entry in entries:
        if should_remove(entry, mode, remove_keys, keep_keys):
            tag.delall(entry.key.split(":", 1)[0]) if ":" not in entry.key else tag.pop(entry.key, None)
            removed.append(entry)
        else:
            kept.append(entry)
    if not kept:
        return None, removed, []
    output = io.BytesIO()
    tag.save(output, v2_version=4, padding=lambda _info: 0)
    return output.getvalue(), removed, kept


def _serialize_id3v1(payload: bytes, mode: CleanMode, remove_keys: tuple[str, ...], keep_keys: tuple[str, ...]) -> tuple[bytes | None, list[MetadataEntry], list[MetadataEntry]]:
    entries = _id3v1_entries(payload)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    mutable = bytearray(payload)
    slices = {"Title": (3, 33), "Artist": (33, 63), "Album": (63, 93), "Year": (93, 97), "Comment": (97, 127)}
    for entry in entries:
        if should_remove(entry, mode, remove_keys, keep_keys):
            start, end = slices[entry.key]
            mutable[start:end] = b"\x00" * (end - start)
            removed.append(entry)
        else:
            kept.append(entry)
    if not kept:
        return None, removed, []
    return bytes(mutable), removed, kept


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    start, end, id3v2, id3v1 = _regions(data, budget)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []

    prefix = b""
    if id3v2 is not None:
        prefix, rem, kep = _serialize_id3v2(id3v2, mode, remove_keys, keep_keys)
        prefix = prefix or b""
        removed.extend(rem)
        kept.extend(kep)

    suffix = b""
    if id3v1 is not None:
        suffix, rem, kep = _serialize_id3v1(id3v1, mode, remove_keys, keep_keys)
        suffix = suffix or b""
        removed.extend(rem)
        kept.extend(kep)

    return prefix + data[start:end] + suffix, tuple(removed), tuple(kept)
