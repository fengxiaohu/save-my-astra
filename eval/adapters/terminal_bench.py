from __future__ import annotations

from eval.suites import Item, load_suite, read_id_file


def load_terminal_bench(slice_name: str | None = None, limit: int | None = None):
    suite = load_suite("terminal-bench-4")
    if slice_name == "phase3a":
        if limit is not None:
            raise ValueError("--limit cannot shorten the fixed terminal-bench-4 phase3a slice")
        ids = read_id_file(suite.path / "phase3a_ids.txt")
        if not ids:
            raise ValueError("terminal-bench-4 phase3a slice is empty")
    elif slice_name in {None, "smoke"}:
        ids = suite.smoke_ids()
    else:
        ids = read_id_file(suite.path / "all_ids.txt") or suite.smoke_ids()
    if limit is not None:
        ids = ids[:limit]
    blocked = set(suite.raw.get("gpu_or_multicontainer") or [])
    items = []
    for item_id in ids:
        if slice_name in {None, "smoke"} and item_id in blocked:
            continue
        items.append(
            Item(
                item_id=item_id,
                prompt=f"Harbor task terminal-bench/{item_id} on terminal-bench/terminal-bench@4.0.0",
                answer=None,
                delegation_expected=suite.expected_for(item_id),
                extra={"harbor_dataset": suite.raw["dataset"]},
            )
        )
    return items
