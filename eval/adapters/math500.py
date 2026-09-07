from __future__ import annotations

from eval.adapters.jsonl_store import read_items


def load_math500(slice_name: str | None = None, limit: int | None = None):
    return read_items("math-500", slice_name, limit)
