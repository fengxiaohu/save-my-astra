from __future__ import annotations

import json
from pathlib import Path

from eval.paths import DATA
from eval.suites import Item, load_suite


def store_path(suite: str) -> Path:
    return DATA / suite / "items.jsonl"


def write_items(suite: str, rows: list[dict]) -> Path:
    path = store_path(suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def fixture_path(suite_name: str) -> Path:
    names = {
        "aime-2025": "aime_one.jsonl",
        "math-500": "math_one.jsonl",
    }
    if suite_name not in names:
        raise FileNotFoundError(suite_name)
    return Path(__file__).resolve().parent.parent / "fixtures" / names[suite_name]


def read_items(suite_name: str, slice_name: str | None, limit: int | None) -> list[Item]:
    suite = load_suite(suite_name)
    path = store_path(suite_name)
    if not path.exists():
        try:
            path = fixture_path(suite_name)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"{store_path(suite_name)} missing. Run: python -m eval download --suite {suite_name}"
            ) from None
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if slice_name == "smoke":
        smoke = set(suite.smoke_ids())
        if smoke:
            filtered = [row for row in rows if str(row["id"]) in smoke]
            if filtered:
                rows = filtered
    elif slice_name and slice_name not in {"full", "all", None}:
        if slice_name.isdigit():
            rows = rows[: int(slice_name)]
    if limit is not None:
        rows = rows[:limit]
    items = []
    for row in rows:
        item_id = str(row["id"])
        items.append(
            Item(
                item_id=item_id,
                prompt=row["prompt"],
                answer=row.get("answer"),
                delegation_expected=suite.expected_for(item_id),
                extra=row.get("extra") or {},
            )
        )
    return items
