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


_QUOTA_PAUSE_REASONS = frozenset(
    {"quota_soft_stop", "quota_hard_stop", "quota_exhausted", "weekly_exhausted"}
)


def is_quota_pause(reason: str | None) -> bool:
    return reason in _QUOTA_PAUSE_REASONS


def checkpoint(out: Path, state: dict, records: list[dict]) -> None:
    """Commit state and records as one checkpoint, then refresh JSON mirrors."""

    payload = {"state": state, "records": records}
    # The checkpoint is the recovery source.  state.json and records.json are
    # intentionally retained as convenient external mirrors for reports/tools.
    save(out / "checkpoint.json", payload)
    save(out / "state.json", state)
    save(out / "records.json", records)


def load_checkpoint(out: Path) -> tuple[dict, list[dict]]:
    """Load the atomic checkpoint, with compatibility for pre-checkpoint runs."""

    checkpoint_path = out / "checkpoint.json"
    if checkpoint_path.exists():
        try:
            payload = json.loads(checkpoint_path.read_text())
            state = payload["state"]
            records = payload["records"]
            if not isinstance(state, dict) or not isinstance(records, list):
                raise ValueError
            return state, records
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("invalid Phase 3A checkpoint") from None
    try:
        state = json.loads((out / "state.json").read_text())
        records = json.loads((out / "records.json").read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        raise ValueError("missing Phase 3A state or records") from None
    if not isinstance(state, dict) or not isinstance(records, list):
        raise ValueError("invalid Phase 3A state or records")
    return state, records


def mark_quota_pause(state: dict, reason: str, sample: dict, *, next_index: int) -> None:
    """Record the bucket that caused a pause; resume requires a later reset."""

    if not is_quota_pause(reason):
        raise ValueError(f"not a quota pause: {reason}")
    weekly = reason == "weekly_exhausted"
    if weekly:
        required = ("weekly_used_percent", "weekly_resets_at", "weekly_window_duration_mins")
        if any(key not in sample for key in required):
            raise ValueError("weekly quota telemetry is incomplete")
        pause_resets_at = sample["weekly_resets_at"]
        pause_used_percent = sample["weekly_used_percent"]
    else:
        pause_resets_at = sample["resets_at"]
        pause_used_percent = sample["used_percent"]
    state["quota_pause"] = {
        "reason": reason,
        "limit_id": sample["limit_id"],
        "window": "weekly" if weekly else "primary",
        "resets_at": pause_resets_at,
        # ``used_percent`` always retains the real five-hour reading.  Weekly
        # usage is kept separately and is never substituted for that field.
        "used_percent": sample["used_percent"],
        "primary_used_percent": sample["used_percent"],
        "primary_resets_at": sample["resets_at"],
        "next_index": next_index,
    }
    if weekly:
        state["quota_pause"]["weekly_used_percent"] = pause_used_percent
        state["quota_pause"]["weekly_resets_at"] = sample["weekly_resets_at"]
    # Old state files used a date marker.  Remove it when writing the new
    # schema so a same-day reset cannot be mistaken for a permanent stop.
    state.pop("hard_stop_day", None)


def resume_quota_pause(state: dict, sample: dict) -> str | None:
    """Return a pause reason until the original bucket has actually reset."""

    pause = state.get("quota_pause")
    # Translate the old marker once so an interrupted pre-checkpoint run gets
    # the same safe reset behavior without losing its original stop reason.
    if pause is None and (state.get("hard_stop_day") or is_quota_pause(state.get("stop_reason"))):
        baseline = state.get("quota_baseline") or {}
        if baseline.get("resets_at") is not None:
            reason = state.get("stop_reason") if is_quota_pause(state.get("stop_reason")) else "quota_hard_stop"
            weekly = reason == "weekly_exhausted"
            if weekly and baseline.get("weekly_resets_at") is None:
                return "weekly_telemetry_unavailable"
            pause = {
                "reason": reason,
                "limit_id": baseline.get("limit_id", sample["limit_id"]),
                "window": "weekly" if weekly else "primary",
                "resets_at": baseline.get("weekly_resets_at") if weekly else baseline["resets_at"],
            }
            state["quota_pause"] = pause
    if pause is None:
        return None
    if sample["limit_id"] != pause.get("limit_id"):
        raise ValueError("quota limit ID changed while waiting for reset")
    weekly = pause.get("window") == "weekly" or pause.get("reason") == "weekly_exhausted"
    if weekly and any(key not in sample for key in (
        "weekly_used_percent", "weekly_resets_at", "weekly_window_duration_mins"
    )):
        return "weekly_telemetry_unavailable"
    paused_resets_at = pause.get("resets_at", 0)
    reset_key = "weekly_resets_at" if weekly else "resets_at"
    used_key = "weekly_used_percent" if weekly else "used_percent"
    # A future-looking resets_at value is not proof that the old bucket has
    # ended.  The observation itself must be at or after that original reset.
    if sample["observed_at"] < paused_resets_at:
        return pause.get("reason") or "quota_hard_stop"
    if sample[reset_key] <= paused_resets_at:
        return pause.get("reason") or "quota_hard_stop"
    # A changed reset timestamp is not enough by itself: require a usable
    # sample from that new window.  At 100 percent, remain paused again.
    if sample[used_key] >= 100:
        pause["reason"] = "weekly_exhausted" if weekly else "quota_exhausted"
        pause["window"] = "weekly" if weekly else "primary"
        pause["resets_at"] = sample[reset_key]
        pause["used_percent"] = sample["used_percent"]
        pause["primary_used_percent"] = sample["used_percent"]
        pause["primary_resets_at"] = sample["resets_at"]
        if weekly:
            pause["weekly_used_percent"] = sample["weekly_used_percent"]
            pause["weekly_resets_at"] = sample["weekly_resets_at"]
        return pause["reason"]
    state.pop("quota_pause", None)
    state.pop("hard_stop_day", None)
    state["quota_baseline"] = sample
    state["quota_window_resumed_at"] = time.time()
    return None


def prepare(out: Path, task_root: Path, codex_version: str) -> Path:
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("prepare requires an empty output directory")
    frozen = freeze_inputs(task_root.resolve(), codex_version)
    manifest = {
        "schema_version": "phase3a.manifest.v1.2", "protocol_id": f"phase3a-{uuid.uuid4().hex[:12]}",
        "created_at": time.time(), "plan_version": "1.2",
        "task_root": str(task_root.resolve()), "codex_version": codex_version,
        "frozen": frozen, "schedule": schedule(), "n": 1,
        "account": "ChatGPT", "runtime": "harbor", "host": platform.platform(),
        "limits": {"agent_seconds": 3600, "setup_seconds": 900, "verifier_seconds": 900,
                   "quota_soft_pp": 20, "quota_hard_pp": 30, "telemetry_max_age_seconds": 120},
        "readiness": "static_only_gate0_required", "usd_estimate": None,
    }
    manifest["freeze_sha256"] = digest(manifest)
    save(out / "manifest.json", manifest)
    checkpoint(out, {"next_index": 0, "stop_reason": None, "resume_allowed": True}, [])
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
    cleanup = record.get("cleanup")
    if record.get("cleanup_error") or (
        isinstance(cleanup, dict) and cleanup.get("complete") is not True
    ):
        # Cleanup failure is permanent even when the supervisor's original
        # reason was quota related.  The record retains that original reason
        # as evidence, while the protocol stops for manual review.
        return "cleanup_error"
    violation = protocol_violation(record["arm"], record["metrics"])
    if violation:
        return violation
    if record["status"] == "budget_aborted" and is_quota_pause(record.get("stop_reason")):
        return record["stop_reason"]
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
    """Run the frozen schedule with resumable quota pauses.

    A quota pause before a trial leaves ``next_index`` untouched.  A quota
    stop during a trial records that partial attempt as ``budget_aborted`` and
    advances to the next planned trial on a later invocation.  Every resumed
    trial gets a new process and attempt ID; no Codex session is continued.
    """

    from eval.phase3_harbor import build_command, cleanup_run, read_result
    from eval.phase3_metrics import collect_events
    from eval.phase3_report import write_phase3_report

    out = out.resolve()
    with run_lock(out), interruption_handlers():
        recover_auth_lease(out / ".auth-lease.json")
        manifest = validate_manifest(out)
        gate = validate_gate0(out, manifest)
        state, records = load_checkpoint(out)
        state.setdefault("next_index", 0)
        state.setdefault("resume_allowed", True)
        if state.get("stop_reason") == "running":
            for record in records:
                if record.get("status") != "running":
                    continue
                record.update(
                    status="budget_aborted",
                    stop_reason="runner_interrupted",
                    partial=True,
                    verifier={"passed": None},
                    metrics={},
                )
                attempt_path = out / "runs" / record["task"] / record["arm"] / record["attempt_id"]
                if not attempt_path.resolve().is_relative_to(out):
                    raise ValueError("invalid interrupted run path")
                record["cleanup"] = cleanup_run(attempt_path)
                save(attempt_path / "result.json", record)
            state.update(stop_reason="runner_interrupted", resume_allowed=False)
            checkpoint(out, state, records)
            return write_phase3_report(out, records, manifest)

        if not state.get("resume_allowed", True):
            raise ValueError(
                f"protocol stopped: {state.get('stop_reason')}; do not mix a new strategy into it"
            )

        gate_hash = digest(gate)
        if state.get("gate0_sha256", gate_hash) != gate_hash:
            raise ValueError("Gate 0 evidence changed after the first launch")
        state["gate0_sha256"] = gate_hash

        def persist(reason: str | None = None, resume: bool = True) -> Path:
            state.update(stop_reason=reason, resume_allowed=resume)
            checkpoint(out, state, records)
            return write_phase3_report(out, records, manifest)

        try:
            sample = read_telemetry(telemetry)
        except TelemetryError:
            return persist("telemetry_unavailable", False)

        baseline = state.get("quota_baseline") or sample
        if baseline.get("limit_id") != sample["limit_id"]:
            return persist("quota_limit_changed", False)

        # A prior hard stop is resumable only after its original bucket has
        # reset and the first sample from that new bucket is below exhaustion.
        if state.get("quota_pause") or state.get("hard_stop_day") or is_quota_pause(state.get("stop_reason")):
            pause_reason = resume_quota_pause(state, sample)
            if pause_reason:
                return persist(pause_reason, is_quota_pause(pause_reason))
            baseline = sample
        elif baseline.get("resets_at") != sample["resets_at"]:
            # A normal restart across a completed window starts a new baseline.
            baseline = sample
        state["quota_baseline"] = baseline
        persist()

        for index in range(int(state["next_index"]), len(manifest["schedule"])):
            step = manifest["schedule"][index]
            try:
                before = read_telemetry(telemetry)
            except TelemetryError:
                return persist("telemetry_unavailable", False)
            if before["limit_id"] != baseline["limit_id"]:
                return persist("quota_limit_changed", False)
            weekly_window_changed = (
                "weekly_resets_at" in before
                and "weekly_resets_at" in baseline
                and before["weekly_resets_at"] != baseline["weekly_resets_at"]
            )
            if before["resets_at"] != baseline["resets_at"] or weekly_window_changed:
                baseline = before
                state["quota_baseline"] = baseline
            reason = quota_decision(before, baseline, launching=True)
            if reason:
                if is_quota_pause(reason):
                    mark_quota_pause(state, reason, before, next_index=index)
                    return persist(reason, True)
                return persist(reason, False)

            for attempt in range(2):
                validate_manifest(out)
                if digest(validate_gate0(out, manifest)) != state["gate0_sha256"]:
                    return persist("gate0_changed", False)
                attempt_id = f"{index + 1:02d}-{attempt + 1}-{uuid.uuid4().hex[:8]}"
                run_dir = out / "runs" / step["task"] / step["arm"] / attempt_id
                run_dir.mkdir(parents=True, exist_ok=False)
                record = {
                    **step,
                    "attempt_id": attempt_id,
                    "status": "running",
                    "replacement_of": None if attempt == 0 else records[-1]["attempt_id"],
                    "verifier": {"passed": None},
                    "metrics": {},
                    "timing": {},
                    "started_at": time.time(),
                    "quota_before": before,
                    "usd_estimate": None,
                }
                records.append(record)
                checkpoint(out, state | {"stop_reason": "running", "resume_allowed": False}, records)
                save(run_dir / "manifest.json", {"freeze_sha256": manifest["freeze_sha256"], **record})
                task_dir = Path(manifest["task_root"]) / step["task"]
                latest_sample = before
                last_checked = 0.0
                process: dict = {"returncode": 1, "stop_reason": None, "end_to_end_seconds": 0}
                supervisor_stop: str | None = None
                secrets: list[str] = []
                cleanup_attempted = False

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
                    try:
                        with temporary_chatgpt_auth(
                            auth_source, lease_file=out / ".auth-lease.json"
                        ) as (auth_path, secrets):
                            env = os.environ.copy()
                            for name in list(env):
                                if name.endswith("API_KEY") or name in {
                                    "OPENAI_BASE_URL",
                                    "OPENAI_API_BASE",
                                    "AZURE_OPENAI_ENDPOINT",
                                }:
                                    env.pop(name)
                            env.update(
                                PHASE3A_AUTH_FILE=str(auth_path),
                                CODEX_HOME=str(config_home),
                                PYTHONPATH=str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
                            )
                            process = supervise(
                                command,
                                cwd=ROOT,
                                env=env,
                                log=run_dir / "raw" / "harbor.log",
                                timeout_seconds=5400,
                                stop_check=stop_check,
                                secrets=secrets,
                            )
                            supervisor_stop = process.get("stop_reason")
                    finally:
                        cleanup_attempted = True
                        try:
                            record["cleanup"] = cleanup_run(run_dir)
                        finally:
                            scrub_artifacts(run_dir, secrets)

                    # A supervisor stop is authoritative.  A missing or late
                    # Harbor result must not turn a partial quota abort into a
                    # verifier pass or erase its original stop reason.
                    try:
                        result = read_result(run_dir)
                    except Exception as exc:
                        result = {
                            "status": "budget_aborted" if supervisor_stop else "observation_incomplete",
                            "verifier": {"passed": None},
                            "events": [],
                            "error_type": type(exc).__name__,
                        }
                    events = result.pop("events", [])
                    record.update(result)
                    record["metrics"] = collect_events(events)
                    (run_dir / "events.jsonl").write_text(
                        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
                    )
                    record["timing"] = {
                        **record.get("timing", {}),
                        "end_to_end_seconds": process.get("end_to_end_seconds"),
                    }
                    if supervisor_stop:
                        record["supervisor_stop_reason"] = supervisor_stop
                        record.update(
                            status="cleanup_error"
                            if supervisor_stop == "descendant_cleanup"
                            else "budget_aborted",
                            stop_reason=supervisor_stop,
                            partial=True,
                            verifier={"passed": None},
                        )
                    elif process.get("returncode") and record.get("status") == "completed":
                        record["status"] = "agent_error"
                    cleanup = record.get("cleanup", {})
                    if cleanup.get("complete") is not True:
                        record["cleanup_error"] = cleanup.get("errors", True)
                        if supervisor_stop:
                            record["supervisor_stop_reason"] = supervisor_stop
                        record["status"] = "cleanup_error"
                except (OSError, ValueError, RuntimeError) as exc:
                    if cleanup_attempted:
                        record.update(
                            status="cleanup_error",
                            cleanup={"complete": False, "errors": [type(exc).__name__]},
                            cleanup_error=type(exc).__name__,
                            error_type=type(exc).__name__,
                            verifier={"passed": None},
                        )
                    else:
                        record.update(
                            status="budget_aborted" if supervisor_stop else "observation_incomplete",
                            error_type=type(exc).__name__,
                            verifier={"passed": None},
                        )
                    if supervisor_stop:
                        record.update(
                            stop_reason=supervisor_stop,
                            supervisor_stop_reason=supervisor_stop,
                            partial=True,
                        )
                    record["metrics"] = collect_events([])
                except BaseException:
                    if cleanup_attempted:
                        record.update(
                            status="cleanup_error",
                            cleanup={"complete": False, "errors": ["exception"]},
                            cleanup_error="exception",
                            verifier={"passed": None},
                            metrics=collect_events([]),
                        )
                        if supervisor_stop:
                            record.update(
                                stop_reason=supervisor_stop,
                                supervisor_stop_reason=supervisor_stop,
                                partial=True,
                            )
                        save(run_dir / "result.json", record)
                        persist("cleanup_error", False)
                        raise
                    record.update(
                        status="budget_aborted",
                        stop_reason=supervisor_stop or "interrupted",
                        partial=True,
                        verifier={"passed": None},
                        metrics=collect_events([]),
                    )
                    save(run_dir / "result.json", record)
                    persist(record["stop_reason"], False)
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
                        if is_quota_pause(retry_stop):
                            # Do not re-enter this loop after a quota pause:
                            # that would reset ``attempt`` and permit an
                            # unbounded replacement chain across invocations.
                            record["manual_review_required"] = True
                            record["retry_stop_reason"] = retry_stop
                            state["manual_review_required"] = True
                            save(run_dir / "result.json", record)
                            return persist(retry_stop, False)
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
                if is_quota_pause(reason) and record.get("status") == "budget_aborted":
                    try:
                        mark_quota_pause(
                            state,
                            reason,
                            record.get("quota_after") or latest_sample,
                            next_index=index + 1,
                        )
                    except ValueError:
                        # A weekly stop without the observer's weekly fields
                        # is incomplete telemetry, never a reason to guess or
                        # release a pause.
                        return persist("weekly_telemetry_unavailable", False)
                    return persist(reason, True)
                return persist(reason, False)
            persist()
        return persist("completed", False)
