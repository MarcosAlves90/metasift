from __future__ import annotations

from dataclasses import dataclass

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget, ensure_file_size

from .common import make_entry

_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mkdn"}
_KEY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    start: int
    end: int
    entry: MetadataEntry


@dataclass(frozen=True, slots=True)
class FrontMatter:
    bom: bool
    opener: str
    lines: list[str]
    body: str
    fields: tuple[Field, ...]
    parse_complete: bool


def matches(_: bytes, path_suffix: str = "") -> bool:
    return path_suffix.casefold() in _SUFFIXES


def _decode(data: bytes) -> tuple[str, bool]:
    bom = data.startswith(b"\xef\xbb\xbf")
    payload = data[3:] if bom else data
    try:
        return payload.decode("utf-8"), bom
    except UnicodeDecodeError as exc:
        raise ValueError("invalid UTF-8 Markdown document") from exc


def _field_start(line: str, delimiter: str) -> tuple[str, str] | None:
    stripped = line.rstrip("\r\n")
    if not stripped or stripped[:1].isspace() or stripped.lstrip().startswith("#"):
        return None
    key, separator, value = stripped.partition(delimiter)
    key = key.strip()
    if not separator or not key or any(character not in _KEY_CHARS for character in key):
        return None
    return key, value.lstrip()


def _is_unparsed_top_level(line: str) -> bool:
    stripped = line.rstrip("\r\n")
    return bool(stripped.strip()) and not stripped[:1].isspace() and not stripped.lstrip().startswith("#")


def _field_starts(lines: list[str], delimiter: str) -> tuple[list[tuple[int, str, str]], bool]:
    starts: list[tuple[int, str, str]] = []
    parse_complete = True
    for index, line in enumerate(lines):
        parsed = _field_start(line, delimiter)
        if parsed is not None:
            key, inline = parsed
            starts.append((index, key, inline))
        elif _is_unparsed_top_level(line):
            parse_complete = False
    return starts, parse_complete


def _build_fields(
    lines: list[str],
    starts: list[tuple[int, str, str]],
    opener: str,
) -> tuple[Field, ...]:
    source = "Markdown YAML front matter" if opener == "---" else "Markdown TOML front matter"
    fields: list[Field] = []
    for index, (start, key, inline) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        raw = "".join(lines[start:end])
        value = inline + " " + "".join(lines[start + 1 : end])
        fields.append(
            Field(
                key,
                start,
                end,
                make_entry(
                    key=key,
                    source=source,
                    size=len(raw.encode("utf-8")),
                    value=value,
                    namespace="markdown-frontmatter",
                    path=f"markdown.frontmatter.{key}",
                ),
            )
        )
    return tuple(fields)


def _parse(data: bytes) -> FrontMatter | None:
    text, bom = _decode(data)
    lines = text.splitlines(keepends=True)
    if not lines:
        return None

    opener = lines[0].rstrip("\r\n")
    if opener not in {"---", "+++"}:
        return None

    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == opener),
        None,
    )
    if closing is None:
        raise ValueError("unterminated Markdown front matter")

    front_matter = lines[1:closing]
    body = "".join(lines[closing + 1 :])
    delimiter = ":" if opener == "---" else "="
    starts, parse_complete = _field_starts(front_matter, delimiter)
    fields = _build_fields(front_matter, starts, opener)
    return FrontMatter(bom, opener, front_matter, body, fields, parse_complete)


def inspect(
    data: bytes,
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[MetadataEntry, ...]:
    ensure_file_size(len(data), budget)
    parsed = _parse(data)
    if parsed is None:
        return ()

    entries = [field.entry for field in parsed.fields]
    if not parsed.parse_complete or not entries:
        raw = "".join(parsed.lines)
        entries.append(
            make_entry(
                key="FrontMatter",
                source="Markdown front matter",
                size=len(raw.encode("utf-8")),
                value=raw,
                namespace="markdown-frontmatter",
                path="markdown.frontmatter",
            )
        )
    return tuple(entries)


def _whole_front_matter_selected(remove_keys: tuple[str, ...]) -> bool:
    selectors = {key.casefold().strip() for key in remove_keys}
    return bool(
        selectors
        & {
            "frontmatter",
            "markdown.frontmatter",
            "markdown-frontmatter.frontmatter",
        }
    )


def _encode_text(text: str, bom: bool) -> bytes:
    encoded = text.encode("utf-8")
    return b"\xef\xbb\xbf" + encoded if bom else encoded


def _whole_block_result(
    parsed: FrontMatter,
    entries: tuple[MetadataEntry, ...],
    mode: CleanMode,
    remove_keys: tuple[str, ...],
    keep_keys: tuple[str, ...],
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]] | None:
    strip_all = mode in {CleanMode.METADATA_MAX, CleanMode.FULL} or _whole_front_matter_selected(remove_keys)
    if not strip_all or not entries:
        return None
    removable = tuple(entry for entry in entries if should_remove(entry, mode, remove_keys, keep_keys))
    if len(removable) != len(entries):
        return None
    return _encode_text(parsed.body, parsed.bom), entries, ()


def _partition_fields(
    fields: tuple[Field, ...],
    mode: CleanMode,
    remove_keys: tuple[str, ...],
    keep_keys: tuple[str, ...],
) -> tuple[list[tuple[int, int]], list[MetadataEntry], list[MetadataEntry]]:
    remove_spans: list[tuple[int, int]] = []
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for field in fields:
        if should_remove(field.entry, mode, remove_keys, keep_keys):
            remove_spans.append((field.start, field.end))
            removed.append(field.entry)
        else:
            kept.append(field.entry)
    return remove_spans, removed, kept


def _retained_lines(lines: list[str], remove_spans: list[tuple[int, int]]) -> list[str]:
    return [
        line
        for index, line in enumerate(lines)
        if not any(start <= index < end for start, end in remove_spans)
    ]


def _render(parsed: FrontMatter, retained: list[str]) -> bytes:
    if not retained:
        return _encode_text(parsed.body, parsed.bom)
    text = parsed.opener + "\n" + "".join(retained) + parsed.opener + "\n" + parsed.body
    return _encode_text(text, parsed.bom)


def clean(
    data: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    ensure_file_size(len(data), budget)
    parsed = _parse(data)
    if parsed is None:
        return data, (), ()

    entries = inspect(data, budget=budget)
    whole_block = _whole_block_result(parsed, entries, mode, remove_keys, keep_keys)
    if whole_block is not None:
        return whole_block

    remove_spans, removed, kept = _partition_fields(parsed.fields, mode, remove_keys, keep_keys)
    if not removed:
        return data, (), entries
    return _render(parsed, _retained_lines(parsed.lines, remove_spans)), tuple(removed), tuple(kept)
