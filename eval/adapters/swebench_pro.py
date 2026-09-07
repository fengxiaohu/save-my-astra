from __future__ import annotations

from eval.adapters.jsonl_store import read_items
from eval.suites import Item, load_suite


def load_swebench_pro(slice_name: str | None = None, limit: int | None = None):
    suite = load_suite("swebench-pro")
    try:
        return read_items("swebench-pro", slice_name, limit)
    except FileNotFoundError:
        ids = suite.smoke_ids()
        if limit is not None:
            ids = ids[:limit]
        return [
            Item(
                item_id=item_id,
                prompt=f"SWE-bench Pro instance {item_id}",
                answer=None,
                delegation_expected=suite.expected_for(item_id),
            )
            for item_id in ids
        ]
