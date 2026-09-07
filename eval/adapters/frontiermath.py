from __future__ import annotations

from eval.adapters.jsonl_store import read_items


def load_frontiermath_public12(slice_name: str | None = None, limit: int | None = None):
    return read_items("frontiermath-public12", slice_name, limit)
