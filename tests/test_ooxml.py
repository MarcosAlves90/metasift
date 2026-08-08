import io
import zipfile

import pytest

from metasift.adapters import ooxml
from metasift.models import CleanMode
from metasift.resource_limits import ResourceBudget


def test_ooxml_inspect_properties_and_hidden_content(sample_files):
    data = sample_files["ooxml"].read_bytes()
    metadata = ooxml.inspect(data)
    keys = {item.key for item in metadata}
    assert {"creator", "title", "description", "created", "Workflow", "Team", "Comments", "CustomXML"} <= keys
    hidden = [item for item in metadata if item.category == "hidden-content"]
    assert hidden and all(not item.removable for item in hidden)

    cleaned, removed, kept = ooxml.clean(data, CleanMode.AI)
    assert {item.key for item in removed} == {"description", "Workflow"}
    assert "creator" in {item.key for item in kept}
    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        assert b"Hello" in zf.read("word/document.xml")
        assert b"ChatGPT" not in zf.read("docProps/core.xml")
        assert "word/comments.xml" in zf.namelist()


def test_ooxml_privacy_max_and_custom(sample_files):
    data = sample_files["ooxml"].read_bytes()
    privacy, removed, _ = ooxml.clean(data, CleanMode.PRIVACY)
    assert {item.key for item in removed} == {"creator", "created"}
    custom, removed, _ = ooxml.clean(data, CleanMode.CUSTOM, remove_keys=("ooxml.custom.Team",))
    assert {item.key for item in removed} == {"Team"}
    maximal, removed, kept = ooxml.clean(data, CleanMode.METADATA_MAX)
    assert len(removed) >= 6
    assert kept == ()
    remaining = ooxml.inspect(maximal)
    assert {item.key for item in remaining} == {"Comments", "CustomXML"}
    with zipfile.ZipFile(io.BytesIO(maximal)) as zf:
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in zf.infolist())


def test_ooxml_rejects_zip_bomb_ratio():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docProps/core.xml", b"<x>" + b"A" * 50_000 + b"</x>")
    budget = ResourceBudget(max_zip_compression_ratio=2, max_zip_entry_bytes=100_000, max_zip_uncompressed_bytes=100_000)
    with pytest.raises(ValueError, match="compression-ratio"):
        ooxml.inspect(out.getvalue(), budget=budget)
