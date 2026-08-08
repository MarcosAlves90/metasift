from __future__ import annotations

from metasift.models import CleanMode, MetadataEntry
from metasift.resource_limits import DEFAULT_BUDGET, ResourceBudget


def matches(_data: bytes, _path_suffix: str = "") -> bool:
    return True


def inspect(_data: bytes, *, budget: ResourceBudget = DEFAULT_BUDGET) -> tuple[MetadataEntry, ...]:
    return ()


def clean(
    data: bytes,
    _mode: CleanMode,
    _remove_keys: tuple[str, ...] = (),
    _keep_keys: tuple[str, ...] = (),
    *,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> tuple[bytes, tuple[MetadataEntry, ...], tuple[MetadataEntry, ...]]:
    return data, (), ()
