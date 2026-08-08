from __future__ import annotations

import importlib.util
import shutil
from typing import Any


def tool_status() -> dict[str, dict[str, Any]]:
    return {
        "Pillow": {"available": importlib.util.find_spec("PIL") is not None, "purpose": "image decode/rebuild and independent image inspection"},
        "Mutagen": {"available": importlib.util.find_spec("mutagen") is not None, "purpose": "frame-level audio metadata inspection/sanitization"},
        "c2pa-python": {"available": importlib.util.find_spec("c2pa") is not None, "purpose": "official C2PA manifest validation"},
        "ExifTool": {"available": shutil.which("exiftool") is not None, "path": shutil.which("exiftool"), "purpose": "independent metadata oracle"},
        "ffprobe": {"available": shutil.which("ffprobe") is not None, "path": shutil.which("ffprobe"), "purpose": "future audio/video metadata oracle"},
        "ffmpeg": {"available": shutil.which("ffmpeg") is not None, "path": shutil.which("ffmpeg"), "purpose": "future bounded audio/video remux backend"},
    }
