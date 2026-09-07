from __future__ import annotations

from eval.adapters.jsonl_store import read_items


def load_aime2025(slice_name: str | None = None, limit: int | None = None):
    return read_items("aime-2025", slice_name, limit)
