from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageCms, PngImagePlugin

from metasift.engine import inspect_file
from metasift.image_rebuild import _apply_jitter, rebuild_image


def _png_with_metadata(path: Path, *, alpha: bool = False) -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "prompt=owl, seed=123, model=stable diffusion")
    info.add_text("Author", "Private Person")
    mode = "RGBA" if alpha else "RGB"
    color = (80, 120, 160, 128) if alpha else (80, 120, 160)
    Image.new(mode, (24, 16), color).save(path, pnginfo=info)


def test_rebuild_same_format_is_default_and_metadata_free(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _png_with_metadata(source)
    result = rebuild_image(source, quality=90, jitter=0)

    assert result.destination.name == "source.rebuilt.png"
    assert result.input_format == result.output_format == "PNG"
    assert result.quality is None
    assert result.sha256_before == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.sha256_after == hashlib.sha256(result.destination.read_bytes()).hexdigest()
    assert result.sha256_before != result.sha256_after
    assert inspect_file(result.destination).metadata == ()
    with Image.open(result.destination) as output:
        assert output.format == "PNG"
        assert output.size == (24, 16)
        assert output.getexif() == {}


def test_rebuild_supports_same_format_and_explicit_conversion(tmp_path: Path) -> None:
    for image_format, suffix in [("JPEG", ".jpg"), ("WEBP", ".webp"), ("AVIF", ".avif")]:
        source = tmp_path / f"source{suffix}"
        Image.new("RGB", (12, 10), (30, 60, 90)).save(source, format=image_format)
        result = rebuild_image(source, quality=92, jitter=1)
        assert result.input_format == image_format
        assert result.output_format == image_format
        assert result.destination.suffix.casefold() == suffix
        assert result.destination.exists()

    source = tmp_path / "convert.png"
    _png_with_metadata(source)
    destination = tmp_path / "converted.jpg"
    result = rebuild_image(source, destination=destination, output_format="jpeg", quality=92)
    assert result.output_format == "JPEG" and destination.exists()
    assert inspect_file(destination).metadata == ()


def test_rebuild_applies_exif_orientation_before_dropping_metadata(tmp_path: Path) -> None:
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (20, 10), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    result = rebuild_image(source, quality=95, jitter=0)
    assert (result.width, result.height) == (10, 20)
    with Image.open(result.destination) as output:
        assert output.size == (10, 20)
        assert output.getexif() == {}


def test_rebuild_preserves_alpha_same_format_and_flattens_on_jpeg_conversion(tmp_path: Path) -> None:
    source = tmp_path / "transparent.png"
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
    for y in range(6):
        for x in range(6):
            image.putpixel((x, y), (0, 255, 0, 255))
    image.save(source)

    same = rebuild_image(source, jitter=0)
    assert same.alpha_preserved
    with Image.open(same.destination) as output:
        assert output.mode == "RGBA"
        assert output.getpixel((15, 15))[3] == 0

    jpeg = rebuild_image(source, destination=tmp_path / "flat.jpg", output_format="jpeg", quality=95, background=(255, 255, 255))
    assert not jpeg.alpha_preserved
    with Image.open(jpeg.destination) as output:
        whiteish = output.convert("RGB").getpixel((15, 15))
        assert min(whiteish) > 235


def test_rebuild_preserves_icc_by_default_and_can_drop_it(tmp_path: Path) -> None:
    source = tmp_path / "icc.jpg"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (8, 8), "blue").save(source, icc_profile=profile)
    kept = rebuild_image(source, jitter=0)
    assert kept.icc_preserved
    with Image.open(kept.destination) as output:
        assert output.info.get("icc_profile")
    dropped = rebuild_image(source, destination=tmp_path / "dropped.jpg", preserve_icc=False)
    assert not dropped.icc_preserved
    assert any("ICC" in warning for warning in dropped.warnings)


def test_rebuild_guards_invalid_options_and_animated_images(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (5, 5), "blue").save(source)
    with pytest.raises(ValueError, match="quality"):
        rebuild_image(source, quality=84)
    with pytest.raises(ValueError, match="jitter"):
        rebuild_image(source, jitter=3)
    with pytest.raises(ValueError, match="destination extension"):
        rebuild_image(source, destination=tmp_path / "wrong.jpg")
    with pytest.raises(ValueError, match="overwrite"):
        rebuild_image(source, destination=source)

    animated = tmp_path / "animated.webp"
    first = Image.new("RGB", (5, 5), "red")
    second = Image.new("RGB", (5, 5), "blue")
    first.save(animated, save_all=True, append_images=[second], duration=10, loop=0, format="WEBP")
    with pytest.raises(ValueError, match="animated"):
        rebuild_image(animated)


def test_rebuild_rejects_unsupported_input_format(tmp_path: Path) -> None:
    source = tmp_path / "source.bmp"
    Image.new("RGB", (5, 5), "blue").save(source)
    with pytest.raises(ValueError, match="unsupported image format"):
        rebuild_image(source)


def test_cli_legacy_image_clean_and_same_format_batch(tmp_path: Path, capsys) -> None:
    from metasift.cli import main

    source = tmp_path / "source.png"
    _png_with_metadata(source)
    output = tmp_path / "single.jpg"
    assert main(["image-clean", str(source), "-o", str(output), "--quality", "91", "--jitter", "1", "--json"]) == 0
    assert '"output_format": "JPEG"' in capsys.readouterr().out
    assert output.exists()

    source_dir = tmp_path / "batch-source"
    source_dir.mkdir()
    _png_with_metadata(source_dir / "a.png")
    Image.new("RGB", (8, 8), "blue").save(source_dir / "b.webp", format="WEBP")
    output_dir = tmp_path / "batch-output"
    assert main(["image-batch", str(source_dir), "--output-dir", str(output_dir)]) == 0
    assert (output_dir / "a.rebuilt.png").exists()
    assert (output_dir / "b.rebuilt.webp").exists()


def test_jitter_is_nonzero_and_bounded_per_rgb_channel(monkeypatch) -> None:
    import metasift.image_rebuild as image_rebuild

    source = Image.new("RGB", (2, 1), (100, 100, 100))
    monkeypatch.setattr(image_rebuild.random, "randbytes", lambda length: bytes([0, 1, 2, 3, 0, 1])[:length])
    changed = _apply_jitter(source, 2)
    deltas = [new - old for old, new in zip(source.tobytes(), changed.tobytes(), strict=True)]
    assert deltas == [-1, -2, 1, 2, -1, -2]
    assert all(1 <= abs(delta) <= 2 for delta in deltas)


def test_rebuild_removes_c2pa_from_decodable_jpeg(tmp_path: Path) -> None:
    import struct

    source = tmp_path / "c2pa.jpg"
    buffer = __import__("io").BytesIO()
    Image.new("RGB", (16, 12), "orange").save(buffer, format="JPEG", quality=95)
    jpeg = buffer.getvalue()
    payload = b"JUMBF c2pa manifest"
    app11 = b"\xff\xeb" + struct.pack(">H", len(payload) + 2) + payload
    source.write_bytes(jpeg[:2] + app11 + jpeg[2:])
    assert "C2PA" in {entry.key for entry in inspect_file(source).metadata}
    result = rebuild_image(source, quality=95, jitter=0)
    assert inspect_file(result.destination).metadata == ()
