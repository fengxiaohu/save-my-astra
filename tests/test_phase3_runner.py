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
