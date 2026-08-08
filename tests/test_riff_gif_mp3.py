import struct

import pytest

from metasift.adapters import gif, mp3, riff, webp
from metasift.models import CleanMode


def _vp8x_flags(data: bytes) -> int:
    chunks = webp.parse(data)
    return next(chunk.payload[0] for chunk in chunks if chunk.chunk_id == b"VP8X")


def test_webp_clean_updates_vp8x_and_preserves_media(sample_files):
    data = sample_files["webp"].read_bytes()
    entries = webp.inspect(data)
    assert {"Artist", "BodySerialNumber", "XMP", "C2PA"} <= {item.key for item in entries}
    assert _vp8x_flags(data) & 0x08
    assert _vp8x_flags(data) & 0x04
    before_media = [chunk.raw for chunk in webp.parse(data) if chunk.chunk_id in {b"VP8 ", b"VP8L", b"ALPH", b"ANMF"}]

    cleaned, removed, kept = webp.clean(data, CleanMode.AI)
    assert {item.key for item in removed} == {"XMP", "C2PA"}
    assert {"Artist", "BodySerialNumber"} <= {item.key for item in kept}
    flags = _vp8x_flags(cleaned)
    assert flags & 0x08
    assert not (flags & 0x04)
    after_media = [chunk.raw for chunk in webp.parse(cleaned) if chunk.chunk_id in {b"VP8 ", b"VP8L", b"ALPH", b"ANMF"}]
    assert before_media == after_media

    maximal, removed, _ = webp.clean(data, CleanMode.METADATA_MAX)
    assert {"Artist", "BodySerialNumber", "XMP", "C2PA"} <= {item.key for item in removed}
    flags = _vp8x_flags(maximal)
    assert not (flags & 0x08) and not (flags & 0x04)


def test_riff_wave_clean_modes():
    def chunk(kind: bytes, payload: bytes) -> bytes:
        raw = kind + struct.pack("<I", len(payload)) + payload
        return raw + (b"\x00" if len(payload) & 1 else b"")

    body = b"WAVE" + chunk(b"fmt ", b"format") + chunk(b"bext", b"author") + chunk(b"data", b"audio")
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    assert {item.key for item in riff.inspect(data)} == {"bext"}
    cleaned, removed, _ = riff.clean(data, CleanMode.PRIVACY)
    assert {item.key for item in removed} == {"bext"}
    assert b"audio" in cleaned
    assert struct.unpack("<I", cleaned[4:8])[0] + 8 == len(cleaned)


def test_riff_rejects_webp_and_truncated():
    assert not riff.matches(b"RIFF\x04\x00\x00\x00WEBP")
    with pytest.raises(ValueError):
        riff.parse(b"RIFF\xff\xff\xff\xffWAVE")


def test_gif_inspect_and_clean(sample_files):
    data = sample_files["gif"].read_bytes()
    assert {item.key for item in gif.inspect(data)} == {"Comment", "C2PA"}
    cleaned, removed, kept = gif.clean(data, CleanMode.AI)
    assert {item.key for item in removed} == {"Comment", "C2PA"}
    assert kept == ()
    assert cleaned.endswith(b"\x3b")
    assert gif.inspect(cleaned) == ()


def test_gif_invalid():
    with pytest.raises(ValueError):
        gif.parse(b"GIF89a")


def test_mp3_frame_level_modes(sample_files):
    data = sample_files["mp3"].read_bytes()
    metadata = mp3.inspect(data)
    keys = {item.key for item in metadata}
    assert {"TIT2", "TPE1", "TXXX:Workflow", "COMM::eng", "Title", "Artist", "Album", "Year"} <= keys

    ai, removed, kept = mp3.clean(data, CleanMode.AI)
    assert {item.key for item in removed} == {"TXXX:Workflow"}
    assert "TIT2" in {item.key for item in kept}
    assert b"audio-frame" in ai

    privacy, removed, _ = mp3.clean(data, CleanMode.PRIVACY)
    assert {"TPE1", "COMM::eng", "Artist"} <= {item.key for item in removed}
    assert b"audio-frame" in privacy

    full, removed, kept = mp3.clean(data, CleanMode.METADATA_MAX)
    assert kept == () and len(removed) >= 8
    assert full.startswith(b"\xff\xfb")
    assert mp3.inspect(full) == ()


def test_mp3_invalid_syncsafe():
    with pytest.raises(ValueError):
        mp3.inspect(b"ID3\x04\x00\x00\x80\x00\x00\x00")
