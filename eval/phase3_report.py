"""Summary and report rendering for the Phase 3A pilot."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = "phase3a.report.v1"
PLANNED_TASKS = (
    "session-window-debug",
    "payments-pipeline-fix",
    "bun-sourcemap-leak",
    "nextjs-performance",
)
ARMS = ("astra-solo", "astra-luna")
NOT_RUN = "not_run"


def _status(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    value = record.get("status")
    return value if isinstance(value, str) and value else None


def _verifier(record: dict[str, Any] | None) -> bool | None:
    if not record or not isinstance(record.get("verifier"), dict):
        return None
    value = record["verifier"].get("passed")
    return value if isinstance(value, bool) else None


def _metrics(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record or not isinstance(record.get("metrics"), dict):
        return {}
    return record["metrics"]


def _attempt_id(record: dict[str, Any]) -> str | None:
    value = record.get("attempt_id")
    return value if isinstance(value, str) and value else None


def _replacement_target(record: dict[str, Any]) -> str | None:
    value = record.get("replacement_of")
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        target = value.get("attempt_id") or value.get("id")
        return target if isinstance(target, str) and target else None
    return None


def _select_effective(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the last replacement per task/arm while retaining all attempts."""

    groups: dict[tuple[str | None, str | None], list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = (record.get("task"), record.get("arm"))
        groups.setdefault(key, []).append((index, record))

    selected: list[dict[str, Any]] = []
    for group in groups.values():
        superseded: set[str] = set()
        for _, record in group:
            target = _replacement_target(record)
            if target:
                superseded.add(target)
        candidates = [
            (index, record)
            for index, record in group
            if _attempt_id(record) not in superseded
        ]
        if not candidates:
            candidates = group
        non_not_run = [item for item in candidates if _status(item[1]) != NOT_RUN]
        chosen = max(non_not_run or candidates, key=lambda item: item[0])
        selected.append(chosen[1])
    selected.sort(
        key=lambda record: (
            str(record.get("task", "")),
            str(record.get("arm", "")),
        )
    )
    return selected


def _record_key(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "task": record.get("task"),
        "arm": record.get("arm"),
        "attempt_id": record.get("attempt_id"),
        "status": record.get("status"),
        "replacement_of": record.get("replacement_of"),
    }


def _active_children(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    children = metrics.get("children")
    if not isinstance(children, list):
        return []
    result: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        result.append(child)
    return result


def _verified_luna(record: dict[str, Any] | None) -> bool | None:
    if record is None or _status(record) == NOT_RUN:
        return None
    metrics = _metrics(record)
    children = _active_children(metrics)
    count = metrics.get("spawn_count")
    if not isinstance(count, int):
        return None
    if count <= 0:
        return False
    if not metrics.get("observation_complete", False):
        return False
    if len(children) < count:
        return False
    for child in children:
        if child.get("model_source") != "runtime":
            return False
        if child.get("model") != "gpt-5.6-luna":
            return False
        parent = child.get("parent_thread_id")
        thread = child.get("thread_id")
        if not isinstance(parent, str) or not parent or parent == thread:
            return False
    return True


def _parent_reduction(
    solo: dict[str, Any] | None,
    routed: dict[str, Any] | None,
) -> float | None:
    solo_tokens = _metrics(solo).get("parent_tokens")
    routed_tokens = _metrics(routed).get("parent_tokens")
    if (
        not isinstance(solo_tokens, int)
        or not isinstance(routed_tokens, int)
        or solo_tokens <= 0
    ):
        return None
    return 1.0 - routed_tokens / solo_tokens


def _pair_quality(
    solo: dict[str, Any] | None,
    routed: dict[str, Any] | None,
) -> str:
    solo_pass = _verifier(solo)
    routed_pass = _verifier(routed)
    if solo_pass is None or routed_pass is None:
        return "unknown"
    if solo_pass and routed_pass:
        return "both_pass"
    if solo_pass and not routed_pass:
        return "solo_pass_routed_fail"
    if not solo_pass and routed_pass:
        return "solo_fail_routed_pass"
    return "both_fail"


def _fully_completed(record: dict[str, Any] | None) -> bool:
    return bool(record and _status(record) == "completed" and _verifier(record) is not None)


def _paired_records(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task: dict[Any, dict[str, dict[str, Any]]] = {}
    for record in selected:
        arm = record.get("arm")
        if arm in ARMS:
            by_task.setdefault(record.get("task"), {})[arm] = record

    result: list[dict[str, Any]] = []
    for task in PLANNED_TASKS:
        arms = by_task.get(task, {})
        solo = arms.get("astra-solo")
        routed = arms.get("astra-luna")
        result.append(
            {
                "task": task,
                "solo": _record_key(solo),
                "routed": _record_key(routed),
                "quality": _pair_quality(solo, routed),
                "parent_reduction": _parent_reduction(solo, routed),
                "verified_luna_delegated": _verified_luna(routed),
                "complete": bool(
                    _fully_completed(solo) and _fully_completed(routed)
                ),
            }
        )
    return result


def summarize_runs(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize all attempts and the effective four task pairs."""

    all_records = [record for record in records if isinstance(record, dict)]
    selected = _select_effective(all_records)
    pairs = _paired_records(selected)

    attempted = [record for record in selected if _status(record) != NOT_RUN]
    completed = [record for record in selected if _status(record) == "completed"]
    not_run = [record for record in selected if _status(record) == NOT_RUN]
    present_keys = {
        (record.get("task"), record.get("arm")) for record in selected
    }
    # The runner may omit future tasks entirely after a routing gate stops the
    # pilot. Synthesize those planned cells as not_run for coverage reporting.
    for task in PLANNED_TASKS:
        for arm in ARMS:
            if (task, arm) not in present_keys:
                not_run.append(
                    {
                        "task": task,
                        "arm": arm,
                        "attempt_id": None,
                        "status": NOT_RUN,
                        "replacement_of": None,
                    }
                )
    treatment = [
        record
        for record in selected
        if record.get("arm") == "astra-luna" and _status(record) != NOT_RUN
    ]
    delegated = [record for record in treatment if _verified_luna(record) is True]

    quality_known = [pair for pair in pairs if pair["quality"] != "unknown"]
    regressions = [
        pair for pair in pairs if pair["quality"] == "solo_pass_routed_fail"
    ]
    quality_passes = len(quality_known) == len(PLANNED_TASKS) and not regressions

    identity_issues: list[dict[str, Any]] = []
    for record in selected:
        if _status(record) == NOT_RUN:
            continue
        metrics = _metrics(record)
        if metrics.get("observation_complete") is not True:
            identity_issues.append(_record_key(record) or {})
        children = _active_children(metrics)
        if not children:
            if isinstance(metrics.get("spawn_count"), int) and metrics["spawn_count"] > 0:
                identity_issues.append(_record_key(record) or {})
            continue
        for child in children:
            if record.get("arm") == "astra-solo":
                identity_issues.append(
                    {**(_record_key(record) or {}), "reason": "baseline_child", "child": child}
                )
            child_ids = {
                str(other.get("thread_id"))
                for other in children
                if other.get("thread_id")
            }
            if (
                child.get("model_source") != "runtime"
                or child.get("model") != "gpt-5.6-luna"
                or not child.get("parent_thread_id")
                or child.get("parent_thread_id") == child.get("thread_id")
                or child.get("parent_thread_id") in child_ids
            ):
                identity_issues.append({**(_record_key(record) or {}), "child": child})

    all_reductions = [
        pair["parent_reduction"]
        for pair in pairs
        if isinstance(pair.get("parent_reduction"), (int, float))
    ]
    passing_reductions: list[float] = []
    for pair in pairs:
        if pair["quality"] != "both_pass":
            continue
        task = pair["task"]
        solo = next(
            (
                record
                for record in selected
                if record.get("task") == task and record.get("arm") == "astra-solo"
            ),
            None,
        )
        routed = next(
            (
                record
                for record in selected
                if record.get("task") == task and record.get("arm") == "astra-luna"
            ),
            None,
        )
        if (
            solo
            and routed
            and _status(solo) == "completed"
            and _status(routed) == "completed"
            and _metrics(solo).get("usage_complete") is True
            and _metrics(routed).get("usage_complete") is True
            and isinstance(_metrics(solo).get("parent_tokens"), int)
            and isinstance(_metrics(routed).get("parent_tokens"), int)
            and isinstance(pair.get("parent_reduction"), (int, float))
        ):
            passing_reductions.append(pair["parent_reduction"])

    completeness = len(pairs) == len(PLANNED_TASKS) and all(
        pair.get("complete") for pair in pairs
    )
    routing_passes = len(delegated) >= 3
    identity_passes = not identity_issues
    parent_signal_passes = len(passing_reductions) >= 3 and statistics.median(
        passing_reductions
    ) > 0
    if (
        completeness
        and routing_passes
        and identity_passes
        and quality_passes
        and parent_signal_passes
    ):
        decision = "feasibility_pass"
    elif regressions or identity_issues:
        decision = "partial"
    else:
        decision = "inconclusive"

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "planned_tasks": list(PLANNED_TASKS),
        "planned_arms": list(ARMS),
        "planned_runs": len(PLANNED_TASKS) * len(ARMS),
        "planned": len(PLANNED_TASKS) * len(ARMS),
        "attempted_runs": len(attempted),
        "attempted": len(attempted),
        "completed_runs": len(completed),
        "completed": len(completed),
        "extra_attempts": max(0, len(all_records) - len(PLANNED_TASKS) * len(ARMS)),
        "not_run": [_record_key(record) for record in not_run],
        "records": all_records,
        "effective_records": selected,
        "pairs": pairs,
        "pairings": pairs,
        "routing": {
            "treatment_attempted": len(treatment),
            "verified_luna_delegated": len(delegated),
            "threshold": 3,
            "passes": routing_passes,
        },
        "identity": {"passes": identity_passes, "issues": identity_issues},
        "quality": {
            "known_pairs": len(quality_known),
            "regressions": regressions,
            "passes": quality_passes,
        },
        "parent_signal": {
            "complete_passing_pairs": len(passing_reductions),
            "reductions": passing_reductions,
            "median_reduction": statistics.median(passing_reductions)
            if passing_reductions
            else None,
            "threshold_pairs": 3,
            "passes": parent_signal_passes,
            "all_pair_reductions": all_reductions,
        },
        "gates": {
            "completeness": completeness,
            "routing": routing_passes,
            "identity": identity_passes,
            "quality": quality_passes,
            "parent_signal": parent_signal_passes,
        },
        "decision": decision,
        "usd_estimate": None,
    }


def _display(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _markdown(summary: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        "# Phase 3A report",
        "",
        f"Decision: {summary.get('decision')}",
        "",
        "## Manifest",
        "",
        "~~~json",
        json.dumps(manifest, indent=2, ensure_ascii=False),
        "~~~",
        "",
        "## Coverage",
        "",
        f"Planned runs: {summary.get('planned_runs')}",
        f"Attempted runs: {summary.get('attempted_runs')}",
        f"Completed runs: {summary.get('completed_runs')}",
        f"Not run: {len(summary.get('not_run') or [])}",
        "",
        "## Task pairs",
        "",
        "| task | solo status | routed status | solo verifier | routed verifier | parent reduction | routed Luna | quality |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for pair in summary.get("pairs", []):
        solo = pair.get("solo") or {}
        routed = pair.get("routed") or {}
        solo_full = next(
            (
                record
                for record in summary.get("effective_records", [])
                if record.get("task") == pair.get("task")
                and record.get("arm") == "astra-solo"
            ),
            None,
        )
        routed_full = next(
            (
                record
                for record in summary.get("effective_records", [])
                if record.get("task") == pair.get("task")
                and record.get("arm") == "astra-luna"
            ),
            None,
        )
        lines.append(
            "| {task} | {solo_status} | {routed_status} | {solo_pass} | {routed_pass} | {reduction} | {luna} | {quality} |".format(
                task=pair.get("task"),
                solo_status=solo.get("status", "not_run"),
                routed_status=routed.get("status", "not_run"),
                solo_pass=_display(_verifier(solo_full)),
                routed_pass=_display(_verifier(routed_full)),
                reduction=_display(pair.get("parent_reduction")),
                luna=_display(pair.get("verified_luna_delegated")),
                quality=pair.get("quality"),
            )
        )
    lines.extend(["", "## Gates", "", "| gate | result |", "| --- | --- |"])
    for key, value in (summary.get("gates") or {}).items():
        lines.append(f"| {key} | {_display(value)} |")
    lines.extend(
        [
            "",
            "## Attempts",
            "",
            "~~~json",
            json.dumps(summary.get("records", []), indent=2, ensure_ascii=False),
            "~~~",
            "",
        ]
    )
    return "\n".join(lines)


def write_phase3_report(
    out: Path,
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> Path:
    """Write REPORT.md and summary.json and return the report path."""

    out = Path(out)
    if (out.exists() and out.is_dir()) or out.suffix == "":
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / "REPORT.md"
        summary_path = out / "summary.json"
    elif out.suffix.lower() == ".json":
        out.parent.mkdir(parents=True, exist_ok=True)
        summary_path = out
        report_path = out.with_suffix(".md")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        report_path = out
        summary_path = out.with_name("summary.json")

    summary = summarize_runs(records)
    summary_payload = {"manifest": manifest, "summary": summary}
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_markdown(summary, manifest), encoding="utf-8")
    return report_path


__all__ = ["summarize_runs", "write_phase3_report"]
