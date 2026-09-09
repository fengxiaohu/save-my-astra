from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.adapters import load_items
from eval.adapters.math_grade import grade_math
from eval.profiles import load_profile
from eval.report import write_report
from eval.routing import classify_routing
from eval.runtimes.api import run_api
from eval.runtimes.codex_cli import run_codex
from eval.runtimes.harbor import run_harbor
from eval.suites import Item, Suite, load_suite
from eval.usage import RunUsage


def _require_positive(name: str, value: int | None) -> None:
    if value is None:
        return
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")


def _run_one(item: Item, profile, runtime: str, run_id: str) -> tuple[str, RunUsage]:
    if runtime == "api":
        return run_api(item, profile)
    if runtime == "codex":
        return run_codex(item, profile, run_id=run_id)
    raise ValueError(f"per-item runtime cannot be {runtime}")


def _grade(suite: Suite, item: Item, text: str) -> bool | None:
    if suite.role == "cost_calibration":
        ok, _ = grade_math(text, item.answer)
        return ok
    return None


def run_arm(
    *,
    suite: Suite,
    items: list[Item],
    profile_name: str,
    runtime: str,
    n: int,
    run_id: str,
    dry_run: bool,
) -> dict:
    profile = load_profile(profile_name)
    records = []
    totals = RunUsage()
    quality_hits = 0
    quality_n = 0
    status_counts: Counter[str] = Counter()

    if runtime == "harbor":
        out = Path(".eval-codex") / run_id / profile.name
        cmd = run_harbor(profile, items, out=out, print_only=True)
        return {
            "name": profile.name,
            "role": profile.role,
            "parent_model": profile.parent_model,
            "command": cmd,
            "note": "Harbor was printed, not executed. Official resolution rate comes from Harbor.",
            "quality": None,
            "total_tokens": 0,
            "usd_estimate": 0,
            "routing_missed": 0,
            "over_delegated": 0,
            "items": [],
        }

    for item in items:
        for attempt in range(n):
            if dry_run:
                text, usage = "", RunUsage()
                usage.add(profile.parent_model, input_tokens=0, output_tokens=0)
            else:
                text, usage = _run_one(item, profile, runtime, f"{run_id}-{attempt}")
            status = classify_routing(item.delegation_expected, usage.spawn_count)
            status_counts[status] += 1
            correct = None if dry_run else _grade(suite, item, text)
            if correct is not None:
                quality_n += 1
                quality_hits += int(correct)
            for model, bucket in usage.by_model.items():
                totals.add(
                    model,
                    input_tokens=bucket.input_tokens,
                    output_tokens=bucket.output_tokens,
                    total_tokens=bucket.total_tokens,
                )
            totals.spawn_count += usage.spawn_count
            records.append(
                {
                    "id": item.item_id,
                    "attempt": attempt,
                    "delegation_expected": item.delegation_expected,
                    "spawn_count": usage.spawn_count,
                    "routing": status,
                    "correct": correct,
                    "usage": usage.as_dict(),
                }
            )

    quality = None
    if quality_n:
        quality = round(quality_hits / quality_n, 4)
    return {
        "name": profile.name,
        "role": profile.role,
        "parent_model": profile.parent_model,
        "quality": quality,
        "total_tokens": totals.total_tokens,
        "usd_estimate": round(totals.usd, 6),
        "usage": totals.as_dict(),
        "routing_missed": status_counts["routing_missed"],
        "over_delegated": status_counts["over_delegated"],
        "routing_ok": status_counts["routing_ok"],
        "items": records,
    }


def run_eval(
    *,
    profile: str,
    baseline: str,
    suite_name: str,
    slice_name: str | None,
    runtime: str,
    n: int | None,
    limit: int | None,
    dry_run: bool,
    print_cmd: bool,
) -> Path | dict:
    _require_positive("n", n)
    _require_positive("limit", limit)
    if suite_name == "terminal-bench-4" and runtime != "harbor":
        raise ValueError("Terminal-Bench requires Harbor task environments and the original verifier")
    if runtime == "harbor" and not (dry_run or print_cmd):
        raise ValueError("Legacy Harbor execution is command-only; use phase3a prepare/execute for verified paired runs")
    suite = load_suite(suite_name)
    items = load_items(suite_name, slice_name, limit)
    repeats = n if n is not None else suite.n_default
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{uuid.uuid4().hex[:12]}"

    if runtime == "harbor" or print_cmd:
        from eval.runtimes.harbor import harbor_command
        from eval.profiles import load_profile as lp

        commands = {
            arm: harbor_command(
                lp(arm),
                items,
                out=Path(".eval-codex") / run_id / arm,
            )
            for arm in (baseline, profile)
        }
        payload = {
            "suite": suite.name,
            "role": suite.role,
            "question": suite.question,
            "profile": profile,
            "baseline": baseline,
            "runtime": "harbor",
            "n": repeats,
            "run_id": run_id,
            "commands": commands,
            "item_ids": [item.item_id for item in items],
        }
        if print_cmd or dry_run:
            path = write_report(payload)
            return path
        raise ValueError("Harbor execution requires the Phase 3A frozen runner")

    arms = [
        run_arm(
            suite=suite,
            items=items,
            profile_name=baseline,
            runtime=runtime,
            n=repeats,
            run_id=run_id,
            dry_run=dry_run,
        ),
        run_arm(
            suite=suite,
            items=items,
            profile_name=profile,
            runtime=runtime,
            n=repeats,
            run_id=run_id,
            dry_run=dry_run,
        ),
    ]
    payload = {
        "suite": suite.name,
        "role": suite.role,
        "question": suite.question,
        "profile": profile,
        "baseline": baseline,
        "runtime": runtime,
        "n": repeats,
        "run_id": run_id,
        "dry_run": dry_run,
        "item_ids": [item.item_id for item in items],
        "arms": arms,
    }
    return write_report(payload)
