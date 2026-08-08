from __future__ import annotations

from collections.abc import MutableMapping

from PIL import ExifTags, Image

from metasift.models import CleanMode, MetadataEntry
from metasift.policy import should_remove
from .common import make_entry

_EXIF_IFD = 34665
_GPS_IFD = 34853
_INTEROP_IFD = 40965
_POINTER_TAGS = {_EXIF_IFD, _GPS_IFD, _INTEROP_IFD}


def _tag_name(tag: int, namespace: str) -> str:
    if namespace == "GPS":
        return ExifTags.GPSTAGS.get(tag, f"Tag0x{tag:04x}")
    return ExifTags.TAGS.get(tag, f"Tag0x{tag:04x}")


def _value_text(value: object) -> str:
    if isinstance(value, bytes):
        return value[:1024].decode("utf-8", errors="replace")
    text = repr(value) if isinstance(value, (tuple, list, dict)) else str(value)
    return text[:2048]


def _entry(tag: int, value: object, namespace: str, source: str) -> MetadataEntry:
    key = _tag_name(tag, namespace)
    path = f"exif.{namespace}.{key}"
    force_privacy = namespace == "GPS" or key.casefold() in {
        "artist",
        "copyright",
        "bodyserialnumber",
        "lensserialnumber",
        "cameraserialnumber",
        "ownername",
        "hostcomputer",
    }
    technical = key.casefold() in {
        "orientation",
        "colorspace",
        "xresolution",
        "yresolution",
        "resolutionunit",
    }
    return make_entry(
        key=key,
        source=source,
        size=len(_value_text(value).encode("utf-8", errors="replace")),
        value=_value_text(value),
        namespace="exif",
        path=path,
        force_privacy=force_privacy,
        preserve_recommended=technical,
        rendering_required=key.casefold() == "orientation",
        removal_impact="rendering-change" if key.casefold() == "orientation" else "metadata-loss",
    )


def load(payload: bytes) -> Image.Exif:
    exif = Image.Exif()
    try:
        exif.load(payload)
    except (SyntaxError, ValueError, TypeError) as exc:
        # Some WebP encoders store bare TIFF bytes rather than the Exif\0\0 prefix.
        if payload.startswith((b"II*\x00", b"MM\x00*")):
            try:
                exif.load(b"Exif\x00\x00" + payload)
            except (SyntaxError, ValueError, TypeError) as nested:
                raise ValueError("invalid EXIF payload") from nested
        else:
            raise ValueError("invalid EXIF payload") from exc
    return exif


def _ifds(exif: Image.Exif) -> list[tuple[str, MutableMapping[int, object]]]:
    mappings: list[tuple[str, MutableMapping[int, object]]] = [("IFD0", exif)]
    for tag, name in ((_EXIF_IFD, "ExifIFD"), (_GPS_IFD, "GPS"), (_INTEROP_IFD, "Interop")):
        if tag not in exif:
            continue
        try:
            nested = exif.get_ifd(tag)
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(nested, MutableMapping):
            mappings.append((name, nested))
    return mappings


def inspect(payload: bytes, *, source: str) -> tuple[MetadataEntry, ...]:
    exif = load(payload)
    entries: list[MetadataEntry] = []
    for namespace, mapping in _ifds(exif):
        for tag, value in list(mapping.items()):
            if namespace == "IFD0" and tag in _POINTER_TAGS:
                continue
            entries.append(_entry(tag, value, namespace, source))
    return tuple(entries)


def clean(
    payload: bytes,
    mode: CleanMode,
    remove_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    *,
    source: str,
) -> tuple[bytes | None, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    exif = load(payload)
    removed: list[MetadataEntry] = []
    kept: list[MetadataEntry] = []
    for namespace, mapping in _ifds(exif):
        for tag, value in list(mapping.items()):
            if namespace == "IFD0" and tag in _POINTER_TAGS:
                continue
            entry = _entry(tag, value, namespace, source)
            if should_remove(entry, mode, remove_keys, keep_keys):
                try:
                    del mapping[tag]
                except KeyError:
                    pass
                removed.append(entry)
            else:
                kept.append(entry)

    if not kept:
        return None, tuple(removed), ()
    return exif.tobytes(), tuple(removed), tuple(kept)
