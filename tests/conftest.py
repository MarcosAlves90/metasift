from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from PIL import Image
from mutagen.id3 import ID3, COMM, TIT2, TPE1, TXXX


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def make_png() -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    prompt = png_chunk(b"tEXt", b"parameters\x00Steps: 20, Sampler: Euler, Model: test")
    author = png_chunk(b"tEXt", b"Author\x00Alice")
    c2pa = png_chunk(b"caBX", b"jumbf-c2pa-manifest")
    idat = png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    iend = png_chunk(b"IEND", b"")
    return signature + ihdr + prompt + author + c2pa + idat + iend


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def valid_exif(*, artist: str = "Alice", serial: str = "SER123", orientation: int | None = None) -> bytes:
    exif = Image.Exif()
    exif[315] = artist
    exif[34665] = {42033: serial}
    if orientation is not None:
        exif[274] = orientation
    return exif.tobytes()


def make_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), (40, 80, 120)).save(buffer, format="JPEG", exif=valid_exif())
    jpeg = buffer.getvalue()
    xmp_xml = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b"<rdf:RDF><rdf:Description><prompt>OpenAI generation</prompt></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    xmp = jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp_xml)
    c2pa1 = jpeg_segment(0xEB, b"JP\x00\x01JUMBF c2pa manifest part 1")
    c2pa2 = jpeg_segment(0xEB, b"manifest part 2")
    comment = jpeg_segment(0xFE, b"ordinary comment")
    return jpeg[:2] + xmp + c2pa1 + c2pa2 + comment + jpeg[2:]


def riff_chunk(kind: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return kind + struct.pack("<I", len(payload)) + payload + pad


def make_webp() -> bytes:
    buffer = io.BytesIO()
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><prompt>ComfyUI workflow</prompt></x:xmpmeta>'
    Image.new("RGB", (8, 6), (30, 60, 90)).save(
        buffer,
        format="WEBP",
        exif=valid_exif(),
        xmp=xmp,
        quality=90,
    )
    raw = buffer.getvalue()
    body = raw[8:] + riff_chunk(b"C2PA", b"manifest")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif_subblocks(payload: bytes) -> bytes:
    parts = []
    for start in range(0, len(payload), 255):
        chunk = payload[start : start + 255]
        parts.append(bytes([len(chunk)]) + chunk)
    return b"".join(parts) + b"\x00"


def make_gif() -> bytes:
    base = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
        b"\x00\x00\x00\xff\xff\xff"
        b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00"
        b"\x02\x02\x44\x01\x00"
    )
    comment = b"\x21\xfe" + gif_subblocks(b"OpenAI generated")
    c2pa = b"\x21\xff\x0bC2PA_GIF\x01\x00\x00" + gif_subblocks(b"manifest")
    return base + comment + c2pa + b"\x3b"


def make_ooxml() -> bytes:
    out = io.BytesIO()
    core = b'''<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">
<dc:creator>Alice</dc:creator><dc:title>Report</dc:title><dc:description>Generated with ChatGPT</dc:description><dcterms:created>2026-08-08</dcterms:created>
</cp:coreProperties>'''
    custom = b'''<?xml version="1.0" encoding="UTF-8"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"><property name="Workflow"><value>ComfyUI</value></property><property name="Team"><value>QA</value></property></Properties>'''
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/custom.xml", custom)
        zf.writestr("word/document.xml", b"<document>Hello</document>")
        zf.writestr("word/comments.xml", b"<comments><comment>hidden note</comment></comments>")
        zf.writestr("customXml/item1.xml", b"<private>value</private>")
    return out.getvalue()


def make_mp3() -> bytes:
    tag = ID3()
    tag.add(TIT2(encoding=3, text=["Title"]))
    tag.add(TPE1(encoding=3, text=["Artist"]))
    tag.add(TXXX(encoding=3, desc="Workflow", text=["OpenAI generator"]))
    tag.add(COMM(encoding=3, lang="eng", desc="", text=["private note"]))
    tag_buffer = io.BytesIO()
    tag.save(tag_buffer, v2_version=4, padding=lambda _info: 0)
    audio = b"\xff\xfb" + b"audio-frame" * 3
    id3v1 = b"TAG" + b"Title".ljust(30, b"\x00") + b"Artist".ljust(30, b"\x00") + b"Album".ljust(30, b"\x00") + b"2026" + b"\x00" * 31
    assert len(id3v1) == 128
    return tag_buffer.getvalue() + audio + id3v1


@pytest.fixture
def sample_files(tmp_path: Path) -> dict[str, Path]:
    data = {
        "png": ("sample.png", make_png()),
        "jpeg": ("sample.jpg", make_jpeg()),
        "webp": ("sample.webp", make_webp()),
        "gif": ("sample.gif", make_gif()),
        "ooxml": ("sample.docx", make_ooxml()),
        "mp3": ("sample.mp3", make_mp3()),
        "unknown": ("sample.bin", b"opaque bytes"),
    }
    result = {}
    for key, (name, payload) in data.items():
        path = tmp_path / name
        path.write_bytes(payload)
        result[key] = path
    return result
