from __future__ import annotations

import json
from typing import Any

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ensure_file_size

from .common import make_entry

_SUFFIXES = {".json"}
_CONTAINERS = {"metadata", "_metadata", "_meta"}


def matches(_: bytes, path_suffix: str = "") -> bool:
    return path_suffix.casefold() in _SUFFIXES


def _loads(data: bytes) -> tuple[Any, bool, bool]:
    bom = data.startswith(b"\xef\xbb\xbf")
    payload = data[3:] if bom else data
    trailing_newline = payload.endswith((b"\n", b"\r"))
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid UTF-8 JSON document") from exc
    return value, bom, trailing_newline


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _entry(container: str, key: str, value: Any) -> MetadataEntry:
    text = _value_text(value)
    return make_entry(
        key=key,
        source=f"JSON {container} object",
        size=len(text.encode("utf-8")),
        value=text,
        namespace="json-metadata",
        path=f"json.{container}.{key}",
    )


def _container_entry(container: str, value: Any) -> MetadataEntry:
    text = _value_text(value)
    return make_entry(
        key=container,
        source="JSON metadata container",
        size=len(text.encode("utf-8")),
        value=text,
        namespace="json-metadata",
        path=f"json.{container}",
    )


def _entries(document: Any) -> list[tuple[str, str | None, MetadataEntry]]:
    if not isinstance(document, dict):
        return []

    rows: list[tuple[str, str | None, MetadataEntry]] = []
    for raw_container, value in document.items():
        container = str(raw_container)
        if container.casefold() not in _CONTAINERS:
            continue
        if isinstance(value, dict):
            rows.extend(
                (container, str(key), _entry(container, str(key), field))
                for key, field in value.items()
            )
        else:
            rows.append((container, None, _container_entry(container, value)))
    return rows


def inspect(
    data: bytes,
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[MetadataEntry, ...]:
    ensure_file_size(len(data), budget)
    document, _, _ = _loads(data)
    return tuple(row[2] for row in _entries(document))


def _remove_metadata_value(document: dict[Any, Any], container: str, key: str | None) -> None:
    if key is None:
        document.pop(container, None)
        return

    target = document.get(container)
    if not isinstance(target, dict):
        return
    target.pop(key, None)
    if not target:
        document.pop(container, None)


def _encode(document: dict[Any, Any], *, bom: bool, trailing_newline: bool) -> bytes:
    text = json.dumps(document, ensure_ascii=False, indent=2)
    if trailing_newline:
        text += "\n"
    encoded = text.encode("utf-8")
    return b"\xef\xbb\xbf" + encoded if bom else encoded


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    ensure_file_size(len(data), budget)
    document, bom, trailing_newline = _loads(data)
    if not isinstance(document, dict):
        return data, (), ()

    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for container, key, entry in _entries(document):
        if not should_remove(entry, mode, remove_keys, keep_keys):
            kept.append(entry)
            continue
        _remove_metadata_value(document, container, key)
        removed.append(entry)

    if not removed:
        return data, (), tuple(kept)
    return _encode(document, bom=bom, trailing_newline=trailing_newline), tuple(removed), tuple(kept)
