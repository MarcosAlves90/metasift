from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """Resource ceilings applied to untrusted file parsing.

    MetaSift 0.3 still uses bounded in-memory adapters. These limits make that
    constraint explicit and fail closed before untrusted inputs can allocate
    without a project-wide ceiling.
    """

    max_file_bytes: int = 512 * 1024 * 1024
    max_metadata_bytes: int = 32 * 1024 * 1024
    max_metadata_entry_bytes: int = 8 * 1024 * 1024
    max_chunks: int = 100_000
    max_xml_bytes: int = 16 * 1024 * 1024
    max_zip_entries: int = 10_000
    max_zip_uncompressed_bytes: int = 512 * 1024 * 1024
    max_zip_entry_bytes: int = 128 * 1024 * 1024
    max_zip_compression_ratio: int = 200
    max_image_pixels: int = 100_000_000
    max_container_depth: int = 32

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items() if hasattr(self, "__dict__") else ():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        # slots dataclasses have no __dict__ on CPython.
        for name in self.__slots__:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


DEFAULT_BUDGET = ResourceBudget()


class ResourceTracker:
    __slots__ = ("budget", "metadata_bytes", "chunks")

    def __init__(self, budget: ResourceBudget = DEFAULT_BUDGET) -> None:
        self.budget = budget
        self.metadata_bytes = 0
        self.chunks = 0

    def chunk(self) -> None:
        self.chunks += 1
        if self.chunks > self.budget.max_chunks:
            raise ValueError(f"container exceeds chunk limit ({self.budget.max_chunks:,})")

    def metadata(self, size: int, *, label: str = "metadata") -> None:
        if size < 0:
            raise ValueError("negative metadata size")
        if size > self.budget.max_metadata_entry_bytes:
            raise ValueError(
                f"{label} exceeds per-entry metadata limit "
                f"({self.budget.max_metadata_entry_bytes:,} bytes)"
            )
        self.metadata_bytes += size
        if self.metadata_bytes > self.budget.max_metadata_bytes:
            raise ValueError(
                f"metadata exceeds total limit ({self.budget.max_metadata_bytes:,} bytes)"
            )


def ensure_file_size(size: int, budget: ResourceBudget = DEFAULT_BUDGET) -> None:
    if size < 0:
        raise ValueError("negative file size")
    if size > budget.max_file_bytes:
        raise ValueError(
            f"file exceeds configured safety limit ({budget.max_file_bytes:,} bytes)"
        )


def ensure_xml_size(size: int, budget: ResourceBudget = DEFAULT_BUDGET) -> None:
    if size > budget.max_xml_bytes:
        raise ValueError(f"XML part exceeds safety limit ({budget.max_xml_bytes:,} bytes)")
