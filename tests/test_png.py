import struct

import pytest

from metasift.adapters import png
from metasift.models import CleanMode
from metasift.resource_limits import ResourceBudget


def test_png_inspect_and_ai_clean(sample_files):
    data = sample_files["png"].read_bytes()
    metadata = png.inspect(data)
    keys = {item.key for item in metadata}
    assert {"parameters", "Author", "C2PA"} <= keys
    cleaned, removed, kept = png.clean(data, CleanMode.AI)
    assert {item.key for item in removed} == {"parameters", "C2PA"}
    assert "Author" in {item.key for item in kept}
    assert b"IDAT" in cleaned
    assert {item.key for item in png.inspect(cleaned)} == {"Author"}


def test_png_metadata_max_and_custom(sample_files):
    data = sample_files["png"].read_bytes()
    cleaned, removed, kept = png.clean(data, CleanMode.METADATA_MAX, keep_keys=("Author",))
    assert "Author" in {item.key for item in kept}
    assert {"parameters", "C2PA"} <= {item.key for item in removed}
    custom, removed, _ = png.clean(data, CleanMode.CUSTOM, remove_keys=("png.text.Author",))
    assert {item.key for item in removed} == {"Author"}
    assert b"parameters" in custom


def test_png_rejects_crc_and_truncation(sample_files):
    data = bytearray(sample_files["png"].read_bytes())
    data[29] ^= 0x01
    with pytest.raises(ValueError, match="CRC"):
        png.parse(bytes(data))
    with pytest.raises(ValueError):
        png.parse(b"not png")
    truncated = sample_files["png"].read_bytes()[:-5]
    with pytest.raises(ValueError):
        png.parse(truncated)


def test_png_bounded_text_decompression():
    import zlib
    from conftest import png_chunk

    bomb = zlib.compress(b"x" * 4096)
    data = png.PNG_SIGNATURE + png_chunk(b"zTXt", b"Comment\x00\x00" + bomb) + png_chunk(b"IEND", b"")
    budget = ResourceBudget(max_metadata_entry_bytes=512, max_metadata_bytes=1024)
    with pytest.raises(ValueError, match="safety limit"):
        png.inspect(data, budget=budget)
