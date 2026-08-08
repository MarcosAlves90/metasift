from __future__ import annotations

import hashlib
import io
import random
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .io_utils import atomic_write
from .models import ImageCleanResult
from .resource_limits import DEFAULT_BUDGET, ResourceBudget, ensure_file_size

_SUPPORTED_INPUTS = {"JPEG", "PNG", "WEBP", "AVIF"}
_OUTPUT_FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF"}
_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "AVIF": ".avif"}
_MIN_QUALITY = 85
_MAX_QUALITY = 95
_MAX_JITTER = 2


def default_image_output_path(source: Path, output_format: str) -> Path:
    suffixes = "".join(source.suffixes)
    stem = source.name[: -len(suffixes)] if suffixes else source.name
    return source.with_name(f"{stem}.rebuilt{_SUFFIXES[output_format]}")


def _resolve_output_format(input_format: str, requested: str) -> str:
    if requested.casefold() == "same":
        return input_format
    try:
        return _OUTPUT_FORMATS[requested.casefold()]
    except KeyError as exc:
        raise ValueError("output format must be one of: same, jpeg, png, webp, avif") from exc


def _prepare_mode(image: Image.Image, output_format: str, background: tuple[int, int, int]) -> tuple[Image.Image, bool]:
    has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    if output_format == "JPEG":
        if has_alpha:
            rgba = image.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, (*background, 255))
            canvas.alpha_composite(rgba)
            return canvas.convert("RGB"), False
        return image.convert("RGB"), False
    if has_alpha:
        return image.convert("RGBA"), True
    if image.mode not in {"RGB", "RGBA", "L", "LA"}:
        return image.convert("RGB"), False
    return image.copy(), False


def _apply_jitter(image: Image.Image, amplitude: int) -> Image.Image:
    if amplitude == 0:
        return image.copy()
    working = image.convert("RGBA") if image.mode == "RGBA" else image.convert("RGB")
    raw = bytearray(working.tobytes())
    channels = 4 if working.mode == "RGBA" else 3
    color_indexes = [index for index in range(len(raw)) if index % channels != 3 or channels == 3]
    noise = random.randbytes(len(color_indexes))
    span = 2 * amplitude
    for token, index in zip(noise, color_indexes, strict=True):
        bucket = token % span
        delta = -(bucket + 1) if bucket < amplitude else bucket - amplitude + 1
        value = raw[index] + delta
        raw[index] = 0 if value < 0 else 255 if value > 255 else value
    return Image.frombytes(working.mode, working.size, bytes(raw))


def rebuild_image(
    path: str | Path,
    *,
    destination: str | Path | None = None,
    output_format: str = "same",
    quality: int = 90,
    jitter: int = 0,
    background: tuple[int, int, int] = (255, 255, 255),
    preserve_icc: bool = True,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> ImageCleanResult:
    """Decode and re-encode an image without carrying ordinary metadata forward.

    Same-format rebuild is the default. Format conversion is explicit. ICC is
    preserved by default because it can materially affect color rendering;
    EXIF/XMP/IPTC/C2PA are not propagated. Pixel jitter is opt-in and is not
    presented as a detector-evasion guarantee.
    """
    if not _MIN_QUALITY <= quality <= _MAX_QUALITY:
        raise ValueError(f"quality must be between {_MIN_QUALITY} and {_MAX_QUALITY}")
    if not 0 <= jitter <= _MAX_JITTER:
        raise ValueError(f"jitter must be between 0 and {_MAX_JITTER}")
    if len(background) != 3 or any(not 0 <= channel <= 255 for channel in background):
        raise ValueError("background must be an RGB tuple with values from 0 to 255")

    source = Path(path)
    source_size = source.stat().st_size
    ensure_file_size(source_size, budget)
    source_bytes = source.read_bytes()
    if len(source_bytes) != source_size:
        raise ValueError("file changed while MetaSift was reading it")

    warnings: list[str] = []
    try:
        with Image.open(io.BytesIO(source_bytes)) as opened:
            input_format = (opened.format or "").upper()
            if input_format not in _SUPPORTED_INPUTS:
                raise ValueError(
                    f"unsupported image format for rebuild: {input_format or 'unknown'}; "
                    f"supported: {', '.join(sorted(_SUPPORTED_INPUTS))}"
                )
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError("animated or multi-frame images are not supported by rebuild strategy")
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > budget.max_image_pixels:
                raise ValueError(f"image dimensions exceed the {budget.max_image_pixels:,}-pixel safety limit")
            icc_profile = opened.info.get("icc_profile") if preserve_icc else None
            had_icc = bool(opened.info.get("icc_profile"))
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            resolved_format = _resolve_output_format(input_format, output_format)
            prepared, alpha_preserved = _prepare_mode(oriented, resolved_format, background)
    except UnidentifiedImageError as exc:
        raise ValueError("input is not a decodable supported image") from exc
    except Image.DecompressionBombError as exc:
        raise ValueError("image exceeds Pillow's decompression-bomb safety limit") from exc

    if had_icc and not preserve_icc:
        warnings.append("ICC profile was intentionally discarded; color rendering may change")
    if input_format != resolved_format:
        warnings.append(f"image format was converted from {input_format} to {resolved_format}")
    if input_format in {"WEBP", "AVIF", "JPEG"}:
        warnings.append("rebuild re-encodes compressed image data; it is not a lossless payload-preservation mode")

    rebuilt = _apply_jitter(prepared, jitter)
    width, height = rebuilt.size
    output = io.BytesIO()
    save_kwargs: dict[str, object] = {}
    if resolved_format in {"JPEG", "WEBP", "AVIF"}:
        save_kwargs["quality"] = quality
    if resolved_format == "JPEG":
        save_kwargs["optimize"] = True
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    # Do not pass exif/xmp/pnginfo/comment: omission is the metadata scrub.
    rebuilt.save(output, format=resolved_format, **save_kwargs)
    output_bytes = output.getvalue()
    ensure_file_size(len(output_bytes), budget)

    target = Path(destination) if destination is not None else default_image_output_path(source, resolved_format)
    expected_suffix = _SUFFIXES[resolved_format]
    if target.suffix.casefold() not in ({".jpg", ".jpeg", ".jpe"} if resolved_format == "JPEG" else {expected_suffix}):
        raise ValueError(f"destination extension must match output format {resolved_format} ({expected_suffix})")
    if target.resolve() == source.resolve():
        raise ValueError("refusing to overwrite source during image rebuild")
    atomic_write(target, output_bytes)

    return ImageCleanResult(
        source=source,
        destination=target,
        input_format=input_format,
        output_format=resolved_format,
        width=width,
        height=height,
        quality=quality if resolved_format in {"JPEG", "WEBP", "AVIF"} else None,
        jitter=jitter,
        bytes_before=len(source_bytes),
        bytes_after=len(output_bytes),
        sha256_before=hashlib.sha256(source_bytes).hexdigest(),
        sha256_after=hashlib.sha256(output_bytes).hexdigest(),
        alpha_preserved=alpha_preserved,
        icc_preserved=bool(icc_profile),
        frames_preserved=1,
        warnings=tuple(warnings),
    )
