"""Frozen, sequential Phase 3A orchestration. Preparation never starts a model."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from eval.paths import ROOT
from eval.phase3_config import TASK_IDS, freeze_inputs, schedule, write_phase3_home
from eval.phase3_process import (TelemetryError, quota_decision, read_telemetry,
                                 scrub_artifacts, supervise, temporary_chatgpt_auth,
                                 interruption_handlers, recover_auth_lease)


def digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def prepare(out: Path, task_root: Path, codex_version: str) -> Path:
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("prepare requires an empty output directory")
    frozen = freeze_inputs(task_root.resolve(), codex_version)
    manifest = {
        "schema_version": "phase3a.manifest.v1", "protocol_id": f"phase3a-{uuid.uuid4().hex[:12]}",
        "created_at": time.time(), "plan_version": "1.1",
        "task_root": str(task_root.resolve()), "codex_version": codex_version,
        "frozen": frozen, "schedule": schedule(), "n": 1,
        "account": "ChatGPT", "runtime": "harbor", "host": platform.platform(),
        "limits": {"agent_seconds": 3600, "setup_seconds": 900, "verifier_seconds": 900,
                   "quota_soft_pp": 20, "quota_hard_pp": 30, "telemetry_max_age_seconds": 120},
        "readiness": "static_only_gate0_required", "usd_estimate": None,
    }
    manifest["freeze_sha256"] = digest(manifest)
    save(out / "manifest.json", manifest)
    save(out / "records.json", [])
    save(out / "state.json", {"next_index": 0, "stop_reason": None, "resume_allowed": True})
    return out / "manifest.json"


def validate_manifest(out: Path) -> dict:
    manifest = json.loads((out / "manifest.json").read_text())
    claimed = manifest.pop("freeze_sha256")
    if digest(manifest) != claimed:
        raise ValueError("manifest modified after freeze")
    manifest["freeze_sha256"] = claimed
    if manifest["schedule"] != schedule():
        raise ValueError("schedule differs from frozen Phase 3A protocol")
    if freeze_inputs(Path(manifest["task_root"]), manifest["codex_version"]) != manifest["frozen"]:
        raise ValueError("source, task inputs or runtime dependencies changed after freeze")
    return manifest


def validate_gate0(out: Path, manifest: dict) -> dict:
    """Require explicit, hash-bound probe artifacts; static checks are not Gate 0."""
    path = out / "preflight" / "gate0.json"
    try:
        gate = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError("Gate 0 evidence missing; prepare does not authorize execution") from None
    if gate.get("freeze_sha256") != manifest["freeze_sha256"]:
        raise ValueError("Gate 0 evidence belongs to another freeze")
    required = {"task_environments", "chatgpt_auth", "effective_models_and_policy",
                "baseline_agents_disabled", "luna_runtime_identity", "verifier_collection",
                "container_cleanup", "quota_and_resource_source"}
    if gate.get("mode") not in {"full", "routing-only"}:
        raise ValueError("Gate 0 mode must be full or routing-only")
    if gate["mode"] == "full":
        required.add("usage_semantics")
    checks = gate.get("checks", {})
    for name in required:
        check = checks.get(name, {})
        if check.get("passed") is not True or not check.get("artifacts"):
            raise ValueError(f"Gate 0 check missing or failed: {name}")
        for artifact in check["artifacts"]:
            candidate = (path.parent / artifact["path"]).resolve()
            if not candidate.is_relative_to(path.parent.resolve()) or not candidate.is_file():
                raise ValueError(f"invalid Gate 0 artifact for {name}")
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != artifact.get("sha256"):
                raise ValueError(f"Gate 0 artifact changed: {name}")
    from eval.phase3_harbor import validate_environment
    environments = gate.get("environments", {})
    if set(environments) != set(TASK_IDS):
        raise ValueError("Gate 0 requires an immutable environment configuration for each task")
    for environment in environments.values():
        validate_environment(environment)
    return gate


@contextmanager
def run_lock(out: Path):
    with (out / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another Phase 3A runner owns this protocol") from None
        yield


def protocol_violation(arm: str, metrics: dict) -> str | None:
    if metrics.get("parent_model") not in (None, "unknown", "gpt-6-astra"):
        return "parent_model_drift"
    if metrics.get("parent_effort") not in (None, "medium"):
        return "parent_effort_drift"
    children = metrics.get("children", [])
    if arm == "astra-solo" and metrics.get("spawn_count", 0):
        return "baseline_created_child"
    for child in children:
        if child.get("model") not in (None, "unknown", "gpt-5.6-luna"):
            return "non_luna_child"
    ids = {child.get("thread_id") for child in children}
    if any(child.get("parent_thread_id") in ids for child in children):
        return "recursive_child"
    return None


def stop_after_record(record: dict) -> str | None:
    violation = protocol_violation(record["arm"], record["metrics"])
    if violation:
        return violation
    if record["status"] in {"model_unavailable", "budget_aborted", "infrastructure_error", "cleanup_error"}:
        return record["status"]
    if not record["metrics"].get("observation_complete"):
        return "observation_incomplete"
    if any(c.get("model") in (None, "unknown") for c in record["metrics"].get("children", [])):
        return "observation_incomplete"
    if any(c.get("model_source") != "runtime" for c in record["metrics"].get("children", [])):
        return "observation_incomplete"
    return None


def gate1(records: list[dict]) -> str | None:
    relevant = [r for r in records if r["task"] in TASK_IDS[:2] and r["arm"] == "astra-luna"]
    latest = {r["task"]: r for r in relevant}
    if len(latest) != 2:
        return "gate1_incomplete"
    for record in latest.values():
        metrics = record["metrics"]
        if metrics.get("observation_complete") and metrics.get("spawn_count", 0) > 0 and all(
                c.get("model") == "gpt-5.6-luna" for c in metrics.get("children", [])):
            return None
    return "routing_gate_failed"


def execute(out: Path, telemetry: Path, auth_source: Path) -> Path:
    """Explicit future execution entry point. Never called by prepare or tests by default."""
    from eval.phase3_harbor import build_command, read_result, cleanup_run
    from eval.phase3_metrics import collect_events
    from eval.phase3_report import write_phase3_report

    out = out.resolve()
    with run_lock(out), interruption_handlers():
        recover_auth_lease(out / ".auth-lease.json")
        manifest = validate_manifest(out)
        gate = validate_gate0(out, manifest)
        state = json.loads((out / "state.json").read_text())
        records = json.loads((out / "records.json").read_text())
        if state.get("stop_reason") == "running":
            for record in records:
                if record["status"] == "running":
                    record.update(status="budget_aborted", stop_reason="runner_interrupted", metrics={})
                    attempt_path = out / "runs" / record["task"] / record["arm"] / record["attempt_id"]
                    if not attempt_path.resolve().is_relative_to(out):
                        raise ValueError("invalid interrupted run path")
                    record["cleanup"] = cleanup_run(attempt_path)
                    save(attempt_path / "result.json", record)
            state.update(stop_reason="runner_interrupted", resume_allowed=False)
            save(out / "records.json", records)
            save(out / "state.json", state)
            return write_phase3_report(out, records, manifest)
        if not state["resume_allowed"]:
            raise ValueError(f"protocol stopped: {state['stop_reason']}; do not mix a new strategy into it")
        gate_hash = digest(gate)
        if state.get("gate0_sha256", gate_hash) != gate_hash:
            raise ValueError("Gate 0 evidence changed after the first launch")
        state["gate0_sha256"] = gate_hash
        sample = read_telemetry(telemetry)
        baseline = state.get("quota_baseline", sample)
        if baseline["limit_id"] != sample["limit_id"]:
            raise ValueError("quota limit ID changed; cannot compare windows")
        if state.get("hard_stop_day") == time.strftime("%Y-%m-%d"):
            raise ValueError("hard stop prevents further experiments today")
        if baseline["resets_at"] != sample["resets_at"]:
            baseline = sample
        state["quota_baseline"] = baseline

        def persist(reason=None, resume=True):
            state.update(stop_reason=reason, resume_allowed=resume)
            save(out / "state.json", state)
            save(out / "records.json", records)
            return write_phase3_report(out, records, manifest)

        persist()
        for index in range(state["next_index"], len(manifest["schedule"])):
            step = manifest["schedule"][index]
            try:
                before = read_telemetry(telemetry)
                reason = quota_decision(before, baseline, launching=True)
            except TelemetryError:
                reason = "telemetry_unavailable"
            if reason:
                if reason == "quota_hard_stop":
                    state["hard_stop_day"] = time.strftime("%Y-%m-%d")
                return persist(reason, True)
            # An interrupted attempt is evidence, never silently retried on resume.
            for attempt in range(2):
                validate_manifest(out)
                if digest(validate_gate0(out, manifest)) != state["gate0_sha256"]:
                    return persist("gate0_changed", False)
                attempt_id = f"{index + 1:02d}-{attempt + 1}-{uuid.uuid4().hex[:8]}"
                run_dir = out / "runs" / step["task"] / step["arm"] / attempt_id
                run_dir.mkdir(parents=True, exist_ok=False)
                record = {**step, "attempt_id": attempt_id, "status": "running",
                          "replacement_of": None if attempt == 0 else records[-1]["attempt_id"],
                          "verifier": {"passed": None}, "metrics": {}, "timing": {},
                          "started_at": time.time(), "quota_before": before, "usd_estimate": None}
                records.append(record)
                persist("running", False)
                save(run_dir / "manifest.json", {"freeze_sha256": manifest["freeze_sha256"], **record})
                task_dir = Path(manifest["task_root"]) / step["task"]
                latest_sample = before
                last_checked = 0.0

                def stop_check():
                    nonlocal latest_sample, last_checked
                    if time.monotonic() - last_checked < 5:
                        return None
                    last_checked = time.monotonic()
                    try:
                        latest_sample = read_telemetry(telemetry)
                    except TelemetryError:
                        return "telemetry_unavailable"
                    return quota_decision(latest_sample, baseline, launching=False)

                try:
                    config_home = write_phase3_home(run_dir / "config", step["arm"])
                    save(run_dir / "environment.json", gate["environments"][step["task"]])
                    command = build_command(task_dir, run_dir, config_home, manifest["codex_version"], 3600)
                    save(run_dir / "command.json", command)
                    with temporary_chatgpt_auth(auth_source, lease_file=out / ".auth-lease.json") as (auth_path, secrets):
                        env = os.environ.copy()
                        for name in list(env):
                            if name.endswith("API_KEY") or name in {"OPENAI_BASE_URL", "OPENAI_API_BASE", "AZURE_OPENAI_ENDPOINT"}:
                                env.pop(name)
                        env.update(PHASE3A_AUTH_FILE=str(auth_path), CODEX_HOME=str(config_home),
                                   PYTHONPATH=str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""))
                        try:
                            process = supervise(command, cwd=ROOT, env=env,
                                                log=run_dir / "raw" / "harbor.log",
                                                timeout_seconds=5400, stop_check=stop_check, secrets=secrets)
                        finally:
                            try:
                                record["cleanup"] = cleanup_run(run_dir)
                            finally:
                                scrub_artifacts(run_dir, secrets)
                    result = read_result(run_dir)
                    events = result.pop("events", [])
                    record.update(result)
                    record["metrics"] = collect_events(events)
                    (run_dir / "events.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events))
                    record["timing"] = {**record.get("timing", {}),
                                        "end_to_end_seconds": process["end_to_end_seconds"]}
                    if process["stop_reason"]:
                        record["status"] = "cleanup_error" if process["stop_reason"] == "descendant_cleanup" else "budget_aborted"
                        record["stop_reason"] = process["stop_reason"]
                        if process["stop_reason"] == "quota_hard_stop":
                            state["hard_stop_day"] = time.strftime("%Y-%m-%d")
                    elif process["returncode"] and record.get("status") == "completed":
                        record["status"] = "agent_error"
                    if record.get("cleanup", {}).get("complete") is not True:
                        record["status"] = "cleanup_error"
                except (OSError, ValueError, RuntimeError) as exc:
                    record.update(status="observation_incomplete", error_type=type(exc).__name__)
                    record["metrics"] = collect_events([])
                except BaseException:
                    record.update(status="budget_aborted", stop_reason="interrupted")
                    record["metrics"] = collect_events([])
                    save(run_dir / "result.json", record)
                    persist("interrupted", False)
                    raise
                record.update(finished_at=time.time(), quota_after=latest_sample)
                save(run_dir / "result.json", record)
                save(run_dir / "usage.json", record["metrics"])
                violation = protocol_violation(step["arm"], record["metrics"])
                if violation:
                    record.update(status="protocol_violation", violation=violation)
                    save(run_dir / "result.json", record)
                if record["status"] == "infrastructure_error" and attempt == 0:
                    try:
                        retry_sample = read_telemetry(telemetry)
                        retry_stop = quota_decision(retry_sample, baseline, launching=True)
                    except TelemetryError:
                        retry_stop = "telemetry_unavailable"
                    if retry_stop:
                        return persist(retry_stop, False)
                    before = retry_sample
                    continue
                break
            state["next_index"] = index + 1
            reason = stop_after_record(record)
            if gate["mode"] == "full" and not record["metrics"].get("usage_complete"):
                reason = reason or "usage_incomplete"
            if not reason and index == 3:
                reason = gate1(records)
            if reason:
                return persist(reason, False)
            persist()
        return persist("completed", False)
