from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from eval.adapters.jsonl_store import write_items
from eval.paths import DATA
from eval.suites import load_suite

HF_ROWS = "https://datasets-server.huggingface.co/rows"


def _hf_rows(dataset: str, split: str, config: str = "default", page: int = 100) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset,
                "config": config,
                "split": split,
                "offset": offset,
                "length": page,
            }
        )
        req = urllib.request.Request(
            f"{HF_ROWS}?{query}",
            headers={"User-Agent": "codex-astra-routing/0.1"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        batch = payload.get("rows") or []
        if not batch:
            break
        for item in batch:
            rows.append(item.get("row") or item)
        offset += len(batch)
        if len(batch) < page:
            break
    return rows


def download_math500() -> Path:
    rows = _hf_rows("HuggingFaceH4/MATH-500", "test")
    items = []
    for i, row in enumerate(rows):
        items.append(
            {
                "id": str(row.get("unique_id") or row.get("problem_id") or i),
                "prompt": row.get("problem") or row.get("question") or "",
                "answer": str(row.get("answer") or row.get("solution") or ""),
            }
        )
    return write_items("math-500", items)


def download_aime2025() -> Path:
    last_error: Exception | None = None
    rows: list[dict] = []
    for dataset, split in (
        ("math-ai/aime25", "test"),
        ("math-ai/aime25", "train"),
        ("opencompass/AIME2025", "test"),
    ):
        try:
            rows = _hf_rows(dataset, split)
            if rows:
                break
        except urllib.error.HTTPError as exc:
            last_error = exc
            continue
    if not rows:
        raise RuntimeError(f"could not download AIME 2025 from Hugging Face: {last_error}")
    items = []
    for i, row in enumerate(rows):
        items.append(
            {
                "id": str(row.get("id") or row.get("problem_id") or f"aime25-{i:02d}"),
                "prompt": row.get("problem") or row.get("question") or "",
                "answer": str(row.get("answer") or row.get("gold") or ""),
            }
        )
    return write_items("aime-2025", items)


def download_swebench_pro() -> Path:
    rows = _hf_rows("ScaleAI/SWE-bench_Pro", "test")
    items = []
    ids = []
    for row in rows:
        item_id = str(row.get("instance_id") or "")
        if not item_id:
            continue
        items.append(
            {
                "id": item_id,
                "prompt": row.get("problem_statement") or row.get("text") or item_id,
                "answer": None,
                "extra": {"repo": row.get("repo")},
            }
        )
        ids.append(item_id)
    path = write_items("swebench-pro", items)
    smoke = load_suite("swebench-pro").path / "smoke_ids.txt"
    if ids and all(line.startswith("#") or not line.strip() for line in smoke.read_text().splitlines()):
        chosen = ids[:8]
        smoke.write_text(
            "# First 8 public IDs from ScaleAI/SWE-bench_Pro. Edit freely.\n"
            + "\n".join(chosen)
            + "\n"
        )
    return path


def download_frontiermath() -> Path:
    dest = DATA / "frontiermath-public12" / "README.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "# FrontierMath public-12\n\n"
        "Only the 12 public problems. Do not copy the private set into this repo.\n\n"
        "Source: https://epoch.ai/benchmarks/frontiermath-tiers-1-3-v2\n\n"
        "Write `items.jsonl` yourself with `{id, prompt, answer}` after you accept Epoch's terms.\n"
    )
    return dest


def download_suite(name: str) -> Path:
    if name == "math-500":
        return download_math500()
    if name == "aime-2025":
        return download_aime2025()
    if name == "swebench-pro":
        return download_swebench_pro()
    if name == "frontiermath-public12":
        return download_frontiermath()
    if name == "terminal-bench-4":
        note = DATA / "terminal-bench-4" / "README.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "TB 4.0 is pulled by Harbor:\n\n"
            "harbor run -d terminal-bench/terminal-bench@4.0.0 --agent oracle\n"
        )
        return note
    raise ValueError(name)
