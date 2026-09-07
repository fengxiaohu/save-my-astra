from __future__ import annotations

import json
import uuid
from pathlib import Path

from eval.paths import REPORTS


def write_report(payload: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    run_id = payload.get("run_id") or uuid.uuid4().hex
    suite = payload.get("suite", "unknown")
    profile = payload.get("profile", "unknown")
    path = REPORTS / f"{run_id}-{suite}-{profile}.json"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    md = path.with_suffix(".md")
    with md.open("x", encoding="utf-8") as handle:
        handle.write(to_markdown(payload))
    return path


def to_markdown(payload: dict) -> str:
    lines = [
        f"# {payload.get('suite')} / {payload.get('profile')} vs {payload.get('baseline')}",
        "",
        f"Role: `{payload.get('role')}`",
        "",
        payload.get("question", ""),
        "",
        f"Run: `{payload.get('run_id')}`",
        "",
        f"Runtime: `{payload.get('runtime')}`  n={payload.get('n')}",
        "",
        "| arm | quality | tokens | usd_est | routing_missed | over_delegated |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for arm in payload.get("arms", []):
        lines.append(
            "| {name} | {quality} | {tokens} | {usd} | {miss} | {over} |".format(
                name=arm.get("name"),
                quality=arm.get("quality"),
                tokens=arm.get("total_tokens"),
                usd=arm.get("usd_estimate"),
                miss=arm.get("routing_missed"),
                over=arm.get("over_delegated"),
            )
        )
    lines.extend(
        [
            "",
            "Quality drop means the routed arm did not win.",
            "AIME / MATH-500 are cost calibration, not coding evidence.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
