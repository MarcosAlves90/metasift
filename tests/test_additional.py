from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from PIL import Image

from conftest import gif_subblocks, jpeg_segment, png_chunk, riff_chunk, valid_exif
from metasift.adapters import generic, gif, jpeg, png, riff, webp
from metasift.cli import main
from metasift.io_utils import atomic_write, default_output_path
from metasift.models import CleanMode
from metasift.resource_limits import ResourceBudget, ResourceTracker


def make_png_with_extra_metadata() -> bytes:
    sig = png.PNG_SIGNATURE
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    ztxt = png_chunk(b"zTXt", b"Software\x00\x00" + zlib.compress(b"Automatic1111"))
    itxt_raw = b"Comment\x00\x00\x00en\x00translated\x00ordinary"
    itxt = png_chunk(b"iTXt", itxt_raw)
    itxt_zip = b"prompt\x00\x01\x00\x00\x00" + zlib.compress(b"ComfyUI graph")
    itxt_compressed = png_chunk(b"iTXt", itxt_zip)
    exif = png_chunk(b"eXIf", valid_exif()[6:])
    timestamp = png_chunk(b"tIME", b"\x07\xea\x08\x08\x00\x30\x00")
    return sig + ihdr + ztxt + itxt + itxt_compressed + exif + timestamp + png_chunk(b"IEND", b"")


def test_png_extra_metadata_paths_and_selective_exif():
    data = make_png_with_extra_metadata()
    entries = png.inspect(data)
    keys = {e.key for e in entries}
    assert {"Software", "Comment", "prompt", "Artist", "BodySerialNumber", "Timestamp"} <= keys
    privacy, removed, _ = png.clean(data, CleanMode.PRIVACY)
    assert {"Artist", "BodySerialNumber", "Timestamp"} <= {e.key for e in removed}
    assert {"Software", "Comment", "prompt"} <= {e.key for e in png.inspect(privacy)}
    ai, removed, _ = png.clean(data, CleanMode.AI)
    assert {e.key for e in removed} == {"Software", "prompt"}


def test_png_text_parser_fail_closed_and_structural_errors():
    with pytest.raises(ValueError, match="zTXt"):
        png._text_payload(b"zTXt", b"broken")
    with pytest.raises(ValueError):
        png._text_payload(b"zTXt", b"x\x00\x00not-zlib")
    with pytest.raises(ValueError, match="iTXt"):
        png._text_payload(b"iTXt", b"bad")
    assert png._text_payload(b"abcd", b"v") == ("abcd", b"v")
    with pytest.raises(ValueError, match="truncated PNG chunk"):
        png.parse(png.PNG_SIGNATURE + b"x")
    with pytest.raises(ValueError, match="truncated PNG payload"):
        png.parse(png.PNG_SIGNATURE + struct.pack(">I", 100) + b"tEXt" + b"x" * 4)
    with pytest.raises(ValueError, match="no IEND"):
        png.parse(png.PNG_SIGNATURE + png_chunk(b"IHDR", b"x"))


def test_png_trailing_bytes_are_reported_and_removable():
    data = png.PNG_SIGNATURE + png_chunk(b"IEND", b"") + b"tail"
    chunks, trailing = png.parse(data)
    assert chunks[-1].chunk_type == b"IEND" and trailing == b"tail"
    assert {entry.key for entry in png.inspect(data)} == {"TrailingData"}
    cleaned, removed, _ = png.clean(data, CleanMode.METADATA_MAX)
    assert {entry.key for entry in removed} == {"TrailingData"}
    assert cleaned.endswith(png_chunk(b"IEND", b""))


def test_jpeg_additional_markers_and_errors():
    data = (
        b"\xff\xd8"
        + jpeg_segment(0xE1, b"unknown-app1")
        + jpeg_segment(0xEB, b"not provenance")
        + jpeg_segment(0xED, b"Photoshop metadata")
        + b"\xff\x01"
        + b"\xff\xd9tail"
    )
    entries = jpeg.inspect(data)
    assert {e.key for e in entries} == {"APP1", "APP11", "IPTC/Photoshop"}
    cleaned, removed, _ = jpeg.clean(data, CleanMode.FULL)
    assert len(removed) == 3 and cleaned.endswith(b"\xff\xd9tail")
    assert jpeg.matches(b"no", ".JPEG")
    for payload in [b"\xff\xd8\xff", b"\xff\xd8\xff\xda", b"\xff\xd8\xff\xda\x00\x01", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xe1\x00\x01", b"\xff\xd8\xff\xe1\x00\x10x"]:
        with pytest.raises(ValueError):
            jpeg.parse(payload)


def test_riff_info_and_metadata_variants():
    body = b"WAVE" + riff_chunk(b"fmt ", b"format") + riff_chunk(b"LIST", b"INFOIARTAlice") + riff_chunk(b"bext", b"author") + riff_chunk(b"data", b"audio")
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    entries = riff.inspect(data)
    assert {e.key for e in entries} == {"RIFF INFO", "bext"}
    cleaned, removed, _ = riff.clean(data, CleanMode.PRIVACY)
    assert {e.key for e in removed} == {"RIFF INFO", "bext"}
    assert b"audio" in cleaned


def make_gif_app(app_id: bytes, payload: bytes) -> bytes:
    header = b"GIF89a\x01\x00\x01\x00\x00\x00\x00"
    app = b"\x21\xff" + bytes([len(app_id)]) + app_id + gif_subblocks(payload)
    return header + app + b"\x3b"


def test_gif_xmp_and_unknown_app_preservation():
    xmp = make_gif_app(b"XMP DataXMP", b"OpenAI")
    assert {e.key for e in gif.inspect(xmp)} == {"XMP"}
    cleaned, removed, _ = gif.clean(xmp, CleanMode.AI)
    assert {e.key for e in removed} == {"XMP"}
    assert gif.inspect(cleaned) == ()
    unknown = make_gif_app(b"NETSCAPE2.0", b"loop")
    cleaned, removed, kept = gif.clean(unknown, CleanMode.FULL)
    assert removed == kept == ()
    assert b"NETSCAPE2.0" in cleaned


def test_gif_error_paths():
    cases = [
        b"notgif",
        b"GIF89a" + b"\x00" * 7 + b"\x99",
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00",
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x21",
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x21\xff",
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x21\xff\x0babc",
    ]
    for payload in cases:
        with pytest.raises(ValueError):
            gif.parse(payload)
    with pytest.raises(ValueError, match="sub-block"):
        gif._subblocks_end(b"\x02x", 0)


def test_resource_tracker_and_io_utils(tmp_path: Path, monkeypatch):
    tracker = ResourceTracker(ResourceBudget(max_chunks=1, max_metadata_bytes=10, max_metadata_entry_bytes=8))
    tracker.chunk()
    with pytest.raises(ValueError, match="chunk limit"):
        tracker.chunk()
    tracker = ResourceTracker(ResourceBudget(max_metadata_bytes=5, max_metadata_entry_bytes=5))
    tracker.metadata(5)
    with pytest.raises(ValueError, match="total limit"):
        tracker.metadata(1)

    assert default_output_path(tmp_path / "README").name == "README.cleaned"
    assert generic.matches(b"anything")
    target = tmp_path / "nested" / "x.bin"
    atomic_write(target, b"ok")
    assert target.read_bytes() == b"ok"

    def boom(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr("metasift.io_utils.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write(tmp_path / "bad.bin", b"x")
    assert not list(tmp_path.glob(".bad.bin.*"))


def test_cli_human_paths_and_errors(sample_files, tmp_path, capsys):
    assert main(["inspect", str(sample_files["png"]), str(sample_files["unknown"])]) == 0
    captured = capsys.readouterr()
    assert "parameters" in captured.out and "warning:" in captured.err
    out = tmp_path / "custom.png"
    assert main(["clean", str(sample_files["png"]), "--mode", "custom", "--remove", "Author", "-o", str(out)]) == 0
    assert "cleaned:" in capsys.readouterr().out
    assert main(["clean", str(sample_files["unknown"]), "--dry-run"]) == 0
    assert "nothing would be removed" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        main(["batch", str(sample_files["png"]), "--in-place", "--output-dir", str(empty)])
