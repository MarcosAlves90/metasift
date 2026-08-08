from pathlib import Path

import pytest

from metasift.cli import main
from metasift.engine import clean_file, inspect_file, plan_file
from metasift.models import CleanMode
from metasift.resource_limits import ResourceBudget


def test_engine_default_output_and_in_place(sample_files):
    source = sample_files["png"]
    report = inspect_file(source)
    assert report.adapter == "png" and report.ai_related_count == 2
    result = clean_file(source, mode=CleanMode.AI)
    assert result.destination.name == "sample.cleaned.png"
    assert result.destination.exists()
    in_place = clean_file(result.destination, mode=CleanMode.METADATA_MAX, in_place=True)
    assert in_place.destination == result.destination
    assert inspect_file(result.destination).metadata == ()


def test_engine_guards_generic_and_file_budget(sample_files, tmp_path):
    with pytest.raises(ValueError):
        clean_file(sample_files["png"], destination=tmp_path / "x.png", in_place=True)
    with pytest.raises(ValueError):
        clean_file(sample_files["png"], mode=CleanMode.CUSTOM)
    report = inspect_file(sample_files["unknown"])
    assert report.adapter == "generic" and report.warnings
    with pytest.raises(ValueError, match="unsupported format"):
        clean_file(sample_files["unknown"], mode=CleanMode.FULL)
    with pytest.raises(ValueError, match="safety limit"):
        inspect_file(sample_files["png"], budget=ResourceBudget(max_file_bytes=10))


def test_plan_share_safe_preserves_provenance(sample_files):
    plan = plan_file(sample_files["png"], mode=CleanMode.SHARE_SAFE)
    assert {entry.key for entry in plan.remove} == {"parameters", "Author"}
    assert "C2PA" in {entry.key for entry in plan.preserve}
    provenance = plan_file(sample_files["png"], mode=CleanMode.PROVENANCE)
    assert {entry.key for entry in provenance.remove} == {"C2PA"}
    assert any("authenticity" in warning for warning in provenance.warnings)


def test_cli_inspect_clean_verify_formats_plan_and_doctor(sample_files, tmp_path, capsys):
    assert main(["inspect", str(sample_files["jpeg"]), "--json"]) == 0
    assert '"adapter": "jpeg"' in capsys.readouterr().out

    assert main(["plan", str(sample_files["png"]), "--preset", "share-safe"]) == 0
    assert "preserve:" in capsys.readouterr().out

    output = tmp_path / "out.jpg"
    assert main(["clean", str(sample_files["jpeg"]), "--mode", "ai", "-o", str(output), "--json"]) == 0
    assert output.exists()
    assert main(["verify", str(output), "--mode", "ai", "--no-independent"]) == 0
    assert "clean:" in capsys.readouterr().out

    assert main(["provenance", str(sample_files["png"]), "--json"]) == 0
    provenance_output = capsys.readouterr().out
    assert '"C2PA"' in provenance_output and '"backend_available": false' in provenance_output

    assert main(["formats"]) == 0
    assert "PNG" in capsys.readouterr().out
    assert main(["doctor"]) == 0
    assert "Pillow" in capsys.readouterr().out


def test_cli_dry_run_batch_and_verify_failure(sample_files, tmp_path, capsys):
    assert main(["clean", str(sample_files["png"]), "--mode", "ai", "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().out

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "a.png").write_bytes(sample_files["png"].read_bytes())
    nested = source_dir / "nested"
    nested.mkdir()
    (nested / "b.jpg").write_bytes(sample_files["jpeg"].read_bytes())
    output_dir = tmp_path / "clean"
    assert main(["batch", str(source_dir), "--recursive", "--mode", "ai", "--output-dir", str(output_dir), "--json"]) == 0
    assert (output_dir / "a.png").exists()
    assert (output_dir / "nested" / "b.jpg").exists()

    assert main(["verify", str(sample_files["png"]), "--mode", "ai", "--json", "--no-independent"]) == 2


def test_batch_no_files_and_unsupported_verify(tmp_path, sample_files, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["batch", str(empty)]) == 1
    assert "no files" in capsys.readouterr().err
    assert main(["verify", str(sample_files["unknown"]), "--mode", "full"]) == 3
    assert "unsupported:" in capsys.readouterr().out
