import pytest

from metasift.adapters import jpeg
from metasift.models import CleanMode


def test_jpeg_inspect_structured_evidence_and_ai_clean(sample_files):
    data = sample_files["jpeg"].read_bytes()
    metadata = jpeg.inspect(data)
    keys = {item.key for item in metadata}
    assert {"prompt", "Artist", "BodySerialNumber", "C2PA", "Comment"} <= keys
    assert next(item for item in metadata if item.key == "prompt").confidence == "probable"
    assert next(item for item in metadata if item.key == "C2PA").provenance_related

    ai_clean, removed, kept = jpeg.clean(data, CleanMode.AI)
    assert {item.key for item in removed} == {"prompt", "C2PA"}
    assert {"Artist", "BodySerialNumber", "Comment"} <= {item.key for item in kept}
    assert b"manifest part 1" not in ai_clean and b"manifest part 2" not in ai_clean
    assert ai_clean.endswith(b"\xff\xd9")


def test_jpeg_privacy_is_tag_level_and_custom(sample_files):
    data = sample_files["jpeg"].read_bytes()
    cleaned, removed, kept = jpeg.clean(data, CleanMode.PRIVACY)
    assert {item.key for item in removed} == {"Artist", "BodySerialNumber"}
    remaining = {item.key for item in jpeg.inspect(cleaned)}
    assert "prompt" in remaining and "C2PA" in remaining and "Comment" in remaining
    assert "Artist" not in remaining and "BodySerialNumber" not in remaining

    custom, removed, _ = jpeg.clean(data, CleanMode.CUSTOM, remove_keys=("jpeg.comment",))
    assert {item.key for item in removed} == {"Comment"}
    assert b"ordinary comment" not in custom
    assert "Artist" in {item.key for item in jpeg.inspect(custom)}


def test_jpeg_metadata_max_removes_all_removable_metadata(sample_files):
    data = sample_files["jpeg"].read_bytes()
    cleaned, removed, kept = jpeg.clean(data, CleanMode.METADATA_MAX)
    assert {item.key for item in removed} >= {"prompt", "Artist", "BodySerialNumber", "C2PA", "Comment"}
    assert kept == ()
    assert jpeg.inspect(cleaned) == ()


def test_jpeg_invalid():
    with pytest.raises(ValueError):
        jpeg.parse(b"bad")
    with pytest.raises(ValueError):
        jpeg.parse(b"\xff\xd8\x00")
