"""Offline orchestration checks. Harbor and model calls are always replaced."""
import hashlib
import json
import time

import pytest

from eval import phase3


def environment_fixture():
    return {task: {"images": {"main": "example.invalid/test@sha256:" + "a" * 64},
                   "cpus": 1, "memory_mb": 256} for task in phase3.TASK_IDS}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(phase3, "freeze_inputs", lambda *a: {"fixture": "immutable"})
    out = tmp_path / "protocol"
    phase3.prepare(out, tmp_path / "tasks", "0.1.0")
    return out


def test_manifest_tampering_is_rejected(prepared):
    manifest = json.loads((prepared / "manifest.json").read_text())
    manifest["limits"]["agent_seconds"] = 8000
    phase3.save(prepared / "manifest.json", manifest)
    with pytest.raises(ValueError, match="modified"):
        phase3.validate_manifest(prepared)


def test_missing_gate0_cannot_start(prepared):
    with pytest.raises(ValueError, match="Gate 0"):
        phase3.validate_gate0(prepared, phase3.validate_manifest(prepared))


def test_gate0_requires_bound_artifacts(prepared):
    manifest = phase3.validate_manifest(prepared)
    directory = prepared / "preflight"
    directory.mkdir()
    artifact = directory / "offline-test-evidence.txt"
    artifact.write_text("synthetic evidence for validator unit test only")
    check = {"passed": True, "artifacts": [{"path": artifact.name,
             "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    keys = ["task_environments", "chatgpt_auth", "effective_models_and_policy",
            "baseline_agents_disabled", "luna_runtime_identity", "verifier_collection",
            "container_cleanup", "quota_and_resource_source", "usage_semantics"]
    phase3.save(directory / "gate0.json", {"freeze_sha256": manifest["freeze_sha256"],
                "mode": "full", "checks": {key: check for key in keys}, "environments": environment_fixture()})
    assert phase3.validate_gate0(prepared, manifest)["mode"] == "full"
    artifact.write_text("changed")
    with pytest.raises(ValueError, match="artifact changed"):
        phase3.validate_gate0(prepared, manifest)


def fixture_metrics(arm, *, spawn=True):
    children = [] if arm == "astra-solo" or not spawn else [
        {"thread_id": "child", "parent_thread_id": "root", "model": "gpt-5.6-luna",
         "model_source": "runtime", "status": "completed"}]
    return {"spawn_count": len(children), "spawn_attempts": len(children), "children": children,
            "observation_complete": True, "usage_complete": True, "parent_tokens": 10,
            "child_tokens": 10 if children else 0, "total_tokens": 20 if children else 10,
            "usd_estimate": None}


def simulate(prepared, tmp_path, monkeypatch, *, spawn=True, clone=False):
    from eval import phase3_harbor, phase3_metrics, phase3_report
    calls = []
    monkeypatch.setattr(phase3, "validate_gate0", lambda *a: {"mode": "full", "environments": environment_fixture()})
    monkeypatch.setattr(phase3_harbor, "build_command", lambda *a: ["NEVER-EXECUTE"])

    def result(path):
        task, arm = path.parts[-3:-1]
        calls.append((task, arm))
        metrics = fixture_metrics(arm, spawn=spawn)
        if clone and metrics["children"]:
            metrics["children"][0]["model"] = "gpt-6-astra"
        return {"status": "completed", "verifier": {"passed": True}, "events": [metrics]}

    monkeypatch.setattr(phase3_harbor, "read_result", result)
    monkeypatch.setattr(phase3_metrics, "collect_events", lambda events: events[0] if events else {})
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    monkeypatch.setattr(phase3, "supervise", lambda *a, **k: {
        "returncode": 0, "stop_reason": None, "end_to_end_seconds": 1})
    telemetry = tmp_path / "telemetry.json"
    now = time.time()
    telemetry.write_text(json.dumps({"observed_at": now, "used_percent": 10,
        "resets_at": now + 10000, "limit_id": "test", "resource_pressure": "normal"}))
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "fake-test-token-12345"}}))
    phase3.execute(prepared, telemetry, auth)
    return calls, json.loads((prepared / "state.json").read_text())


def test_all_eight_runs_follow_frozen_order(prepared, tmp_path, monkeypatch):
    calls, state = simulate(prepared, tmp_path, monkeypatch)
    assert calls == [(s["task"], s["arm"]) for s in phase3.schedule()]
    assert state["stop_reason"] == "completed"
    assert len(list((prepared / "runs").glob("*/*/*/config"))) == 8


def test_gate1_stops_after_four_without_spawn(prepared, tmp_path, monkeypatch):
    calls, state = simulate(prepared, tmp_path, monkeypatch, spawn=False)
    assert len(calls) == 4
    assert state["stop_reason"] == "routing_gate_failed"
    assert state["resume_allowed"] is False


def test_clone_stops_and_preserves_record(prepared, tmp_path, monkeypatch):
    calls, state = simulate(prepared, tmp_path, monkeypatch, clone=True)
    assert len(calls) == 2
    assert state["stop_reason"] == "non_luna_child"
    records = json.loads((prepared / "records.json").read_text())
    assert records[-1]["status"] == "protocol_violation"


def test_unknown_child_is_not_a_verified_luna():
    metrics = fixture_metrics("astra-luna")
    metrics["children"][0]["model"] = None
    assert phase3.stop_after_record({"arm": "astra-luna", "status": "completed", "metrics": metrics}) == "observation_incomplete"


def test_cleanup_failure_blocks_quota_resume_and_preserves_reason():
    metrics = fixture_metrics("astra-luna")
    record = {
        "arm": "astra-luna",
        "status": "budget_aborted",
        "stop_reason": "quota_hard_stop",
        "cleanup": {"complete": False, "errors": ["containers_cleanup_failed"]},
        "metrics": metrics,
    }
    assert phase3.stop_after_record(record) == "cleanup_error"
    assert record["stop_reason"] == "quota_hard_stop"


def test_gate1_ignores_aborted_baseline_when_treatment_is_complete():
    complete = fixture_metrics("astra-luna")
    records = [
        {"task": phase3.TASK_IDS[0], "arm": "astra-solo", "status": "budget_aborted", "metrics": {}},
        {"task": phase3.TASK_IDS[0], "arm": "astra-luna", "status": "completed", "metrics": complete},
        {"task": phase3.TASK_IDS[1], "arm": "astra-solo", "status": "budget_aborted", "metrics": {}},
        {"task": phase3.TASK_IDS[1], "arm": "astra-luna", "status": "completed", "metrics": complete},
    ]
    assert phase3.gate1(records) is None


def test_checkpoint_is_preferred_over_legacy_mirrors(prepared):
    state, records = phase3.load_checkpoint(prepared)
    state["next_index"] = 3
    records.append({"task": "checkpoint-only"})
    phase3.checkpoint(prepared, state, records)
    # Simulate a stale external mirror. Recovery must use the atomic pair.
    phase3.save(prepared / "state.json", {"next_index": 0})
    phase3.save(prepared / "records.json", [])
    restored_state, restored_records = phase3.load_checkpoint(prepared)
    assert restored_state["next_index"] == 3
    assert restored_records == [{"task": "checkpoint-only"}]


def test_pretrial_quota_pause_does_not_advance_index(prepared, tmp_path, monkeypatch):
    from eval import phase3_harbor, phase3_report

    monkeypatch.setattr(phase3, "validate_gate0", lambda *a: {"mode": "full", "environments": environment_fixture()})
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    state, records = phase3.load_checkpoint(prepared)
    baseline = {"observed_at": time.time(), "used_percent": 10, "resets_at": 5000,
                "limit_id": "test", "resource_pressure": "normal"}
    state["quota_baseline"] = baseline
    phase3.checkpoint(prepared, state, records)
    before = {**baseline, "used_percent": 30}
    monkeypatch.setattr(phase3, "read_telemetry", lambda *a, **k: before)
    monkeypatch.setattr(phase3_harbor, "read_result", lambda *a: pytest.fail("must not start a trial"))
    phase3.execute(prepared, tmp_path / "telemetry.json", tmp_path / "auth.json")
    state, records = phase3.load_checkpoint(prepared)
    assert state["next_index"] == 0
    assert state["stop_reason"] == "quota_soft_stop"
    assert state["resume_allowed"] is True
    assert state["quota_pause"]["resets_at"] == before["resets_at"]
    assert records == []


def test_weekly_pause_does_not_use_primary_reset_or_start_trial(prepared, tmp_path, monkeypatch):
    from eval import phase3_harbor, phase3_report

    monkeypatch.setattr(
        phase3,
        "validate_gate0",
        lambda *a: {"mode": "full", "environments": environment_fixture()},
    )
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    baseline = {
        "schema_version": "phase3a.telemetry.v1",
        "observed_at": time.time(),
        "used_percent": 10,
        "resets_at": 5000,
        "limit_id": "test",
        "resource_pressure": "normal",
        "weekly_used_percent": 20,
        "weekly_resets_at": 9000,
        "weekly_window_duration_mins": 10080,
    }
    state, records = phase3.load_checkpoint(prepared)
    state["quota_baseline"] = baseline
    phase3.checkpoint(prepared, state, records)
    before = {**baseline, "used_percent": 41, "weekly_used_percent": 100}
    monkeypatch.setattr(phase3, "read_telemetry", lambda *a, **k: before)
    monkeypatch.setattr(phase3_harbor, "read_result", lambda *a: pytest.fail("must not start a trial"))

    phase3.execute(prepared, tmp_path / "telemetry.json", tmp_path / "auth.json")
    state, records = phase3.load_checkpoint(prepared)
    assert state["next_index"] == 0
    assert state["stop_reason"] == "weekly_exhausted"
    assert state["resume_allowed"] is True
    assert state["quota_pause"]["window"] == "weekly"
    assert state["quota_pause"]["resets_at"] == before["weekly_resets_at"]
    assert state["quota_pause"]["used_percent"] == before["used_percent"]
    assert state["quota_pause"]["weekly_used_percent"] == before["weekly_used_percent"]
    assert state["quota_pause"]["primary_used_percent"] == before["used_percent"]
    assert records == []


def test_live_observer_schema_missing_weekly_stops_before_launch(prepared, tmp_path, monkeypatch):
    from eval import phase3_report

    monkeypatch.setattr(
        phase3,
        "validate_gate0",
        lambda *a: {"mode": "full", "environments": environment_fixture()},
    )
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    now = time.time()
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text(
        json.dumps(
            {
                "schema_version": "phase3a.telemetry.v1",
                "observed_at": now,
                "used_percent": 10,
                "resets_at": now + 10000,
                "limit_id": "test",
                "resource_pressure": "normal",
            }
        )
    )
    phase3.execute(prepared, telemetry, tmp_path / "auth.json")
    state, records = phase3.load_checkpoint(prepared)
    assert state["stop_reason"] == "telemetry_unavailable"
    assert state["resume_allowed"] is False
    assert records == []


def test_quota_abort_keeps_partial_record_and_null_verifier(prepared, tmp_path, monkeypatch):
    from eval import phase3_harbor, phase3_metrics, phase3_report

    monkeypatch.setattr(phase3, "validate_gate0", lambda *a: {"mode": "routing-only", "environments": environment_fixture()})
    monkeypatch.setattr(phase3_harbor, "build_command", lambda *a: ["NEVER-EXECUTE"])
    monkeypatch.setattr(phase3_harbor, "read_result", lambda *a: (_ for _ in ()).throw(RuntimeError("missing result")))
    monkeypatch.setattr(phase3_metrics, "collect_events", lambda events: {})
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    monkeypatch.setattr(phase3, "supervise", lambda *a, **k: {
        "returncode": 1, "stop_reason": "quota_hard_stop", "end_to_end_seconds": 1,
    })
    now = time.time()
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text(json.dumps({"observed_at": now, "used_percent": 10,
        "resets_at": now + 10000, "limit_id": "test", "resource_pressure": "normal"}))
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "fake-test-token-12345"}}))
    phase3.execute(prepared, telemetry, auth)
    state, records = phase3.load_checkpoint(prepared)
    assert state["next_index"] == 1
    assert state["stop_reason"] == "quota_hard_stop"
    assert state["resume_allowed"] is True
    assert records[0]["status"] == "budget_aborted"
    assert records[0]["stop_reason"] == "quota_hard_stop"
    assert records[0]["verifier"]["passed"] is None


@pytest.mark.parametrize("cleanup_mode", ["incomplete", "raises"])
def test_quota_abort_with_cleanup_failure_is_permanent(
    prepared, tmp_path, monkeypatch, cleanup_mode
):
    from eval import phase3_harbor, phase3_metrics, phase3_report

    monkeypatch.setattr(
        phase3, "validate_gate0",
        lambda *a: {"mode": "routing-only", "environments": environment_fixture()},
    )
    monkeypatch.setattr(phase3_harbor, "build_command", lambda *a: ["NEVER-EXECUTE"])
    monkeypatch.setattr(
        phase3_harbor,
        "read_result",
        lambda *a: {"status": "completed", "verifier": {"passed": True}, "events": []},
    )
    if cleanup_mode == "incomplete":
        cleanup = lambda *a: {"complete": False, "errors": ["containers_cleanup_failed"]}
    else:
        def cleanup(*a):
            raise RuntimeError("cleanup unavailable")
    monkeypatch.setattr(phase3_harbor, "cleanup_run", cleanup)
    monkeypatch.setattr(phase3_metrics, "collect_events", lambda events: {})
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    monkeypatch.setattr(
        phase3,
        "supervise",
        lambda *a, **k: {
            "returncode": 1,
            "stop_reason": "quota_hard_stop",
            "end_to_end_seconds": 1,
        },
    )
    now = time.time()
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text(
        json.dumps(
            {
                "observed_at": now,
                "used_percent": 10,
                "resets_at": now + 10000,
                "limit_id": "test",
                "resource_pressure": "normal",
            }
        )
    )
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {"auth_mode": "chatgpt", "tokens": {"access_token": "fake-test-token-12345"}}
        )
    )

    phase3.execute(prepared, telemetry, auth)
    state, records = phase3.load_checkpoint(prepared)
    assert state["stop_reason"] == "cleanup_error"
    assert state["resume_allowed"] is False
    assert "quota_pause" not in state
    assert records[0]["status"] == "cleanup_error"
    assert records[0]["stop_reason"] == "quota_hard_stop"
    assert records[0]["supervisor_stop_reason"] == "quota_hard_stop"
    assert records[0]["verifier"]["passed"] is None


def test_quota_retry_stop_requires_manual_review_instead_of_restarting_replacement(
    prepared, tmp_path, monkeypatch
):
    from eval import phase3_harbor, phase3_metrics, phase3_report

    monkeypatch.setattr(
        phase3, "validate_gate0",
        lambda *a: {"mode": "routing-only", "environments": environment_fixture()},
    )
    monkeypatch.setattr(phase3_harbor, "build_command", lambda *a: ["NEVER-EXECUTE"])
    monkeypatch.setattr(
        phase3_harbor,
        "read_result",
        lambda *a: {"status": "infrastructure_error", "verifier": {"passed": None}, "events": []},
    )
    monkeypatch.setattr(phase3_harbor, "cleanup_run", lambda *a: {"complete": True})
    monkeypatch.setattr(phase3_metrics, "collect_events", lambda events: {})
    monkeypatch.setattr(phase3_report, "write_phase3_report", lambda out, *a: out / "REPORT.md")
    monkeypatch.setattr(
        phase3,
        "supervise",
        lambda *a, **k: {"returncode": 1, "stop_reason": None, "end_to_end_seconds": 1},
    )
    now = time.time()
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text(
        json.dumps(
            {
                "observed_at": now,
                "used_percent": 10,
                "resets_at": now + 10000,
                "limit_id": "test",
                "resource_pressure": "normal",
            }
        )
    )
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {"auth_mode": "chatgpt", "tokens": {"access_token": "fake-test-token-12345"}}
        )
    )

    # The retry telemetry crosses the soft launch threshold.  A subsequent
    # invocation must remain stopped rather than resetting ``attempt``.
    samples = iter(
        [
            {"observed_at": now, "used_percent": 10, "resets_at": now + 10000,
             "limit_id": "test", "resource_pressure": "normal"},
            {"observed_at": now, "used_percent": 10, "resets_at": now + 10000,
             "limit_id": "test", "resource_pressure": "normal"},
            {"observed_at": now, "used_percent": 30, "resets_at": now + 10000,
             "limit_id": "test", "resource_pressure": "normal"},
        ]
    )
    monkeypatch.setattr(phase3, "read_telemetry", lambda *a, **k: next(samples))

    phase3.execute(prepared, telemetry, auth)
    state, records = phase3.load_checkpoint(prepared)
    assert state["stop_reason"] == "quota_soft_stop"
    assert state["resume_allowed"] is False
    assert state["manual_review_required"] is True
    assert len(records) == 1
    assert records[0]["status"] == "infrastructure_error"
    assert records[0]["manual_review_required"] is True
    assert records[0]["retry_stop_reason"] == "quota_soft_stop"
