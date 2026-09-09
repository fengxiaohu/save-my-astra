import asyncio
import importlib
import json
import sys
import types
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from eval.phase3_config import write_phase3_home
from eval.phase3_harbor import build_command, cleanup_run, read_result, session_events
from eval.phase3_metrics import collect_events


def test_job_pins_single_task_limits_and_environment(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text("[environment]\nbuild_timeout_sec=1800\n")
    run = tmp_path / "run"
    run.mkdir()
    home = write_phase3_home(run / "config", "astra-luna")
    (run / "environment.json").write_text(json.dumps({"images": {"main": "example.invalid/image@sha256:" + "a" * 64}, "cpus": 2, "memory_mb": 1024}))
    command = build_command(task, run, home, "0.123.0", 3600)
    assert command[:3] == ["harbor", "run", "--config"]
    config = json.loads((run / "harbor-job.json").read_text())
    assert config["tasks"] == [{"path": str(task)}]
    assert config["agents"][0]["max_timeout_sec"] == 3600
    assert config["agents"][0]["kwargs"]["version"] == "0.123.0"
    assert config["verifier"]["max_timeout_sec"] == 900
    assert config["environment_build_timeout_multiplier"] == .5
    assert config["n_concurrent_trials"] == config["n_attempts"] == 1
    assert config["retry"]["max_retries"] == 0
    assert config["environment"]["override_memory_mb"] == 1024


def native_session(directory, tid, parent, model, total):
    rows = [{"type": "session_meta", "payload": {"id": tid, "source":
            {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "exec"}},
            {"type": "turn_context", "payload": {"model": model, "effort": "max" if parent else "medium"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": total - 1, "output_tokens": 1, "total_tokens": total},
                "last_token_usage": {"input_tokens": total - 1, "output_tokens": 1, "total_tokens": total}}}}]
    file = directory / (tid + ".jsonl")
    file.write_text("\n".join(json.dumps(row) for row in rows))


def test_native_child_rollouts_are_not_counted_as_root_usage(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    native_session(sessions, "root", None, "gpt-6-astra", 100)
    native_session(sessions, "child", "root", "gpt-5.6-luna", 30)
    (tmp_path / "phase3-collection.json").write_text('{"complete":true}')
    metrics = collect_events(session_events(tmp_path))
    assert metrics["spawn_count"] == 1
    assert metrics["children"][0]["model"] == "gpt-5.6-luna"
    assert metrics["parent_model"] == "gpt-6-astra"
    assert metrics["parent_effort"] == "medium"
    assert metrics["parent_tokens"] == 100
    assert metrics["child_tokens"] == 30
    assert metrics["total_tokens"] == 130
    assert metrics["observation_complete"] is True


def test_missing_trial_and_nonbinary_reward_are_not_passes(tmp_path):
    assert read_result(tmp_path)["verifier"]["passed"] is None
    trial = tmp_path / "harbor" / "trial" / "one"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({"trial_name": "one", "task_name": "test", "verifier_result": {"rewards": {"reward": .5}}}))
    assert read_result(tmp_path)["verifier"]["passed"] is None


def test_cleanup_never_lists_unowned_docker_resources(tmp_path, monkeypatch):
    from eval import phase3_harbor
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(phase3_harbor.subprocess, "run", run)
    assert cleanup_run(tmp_path)["complete"] is True
    assert not calls
    (tmp_path / "owned-projects.json").write_text('["phase3-owned"]')
    assert cleanup_run(tmp_path)["complete"] is True
    assert len(calls) == 3
    assert all("label=com.docker.compose.project=phase3-owned" in call for call in calls)


@pytest.fixture
def agent_module(monkeypatch):
    """Replace Harbor APIs with async fakes; never import or run a real backend."""
    modules = ["harbor", "harbor.agents", "harbor.agents.installed", "harbor.agents.installed.codex",
               "harbor.environments", "harbor.environments.docker", "harbor.environments.docker.docker",
               "harbor.models", "harbor.models.job", "harbor.models.job.config", "harbor.models.trial",
               "harbor.models.trial.config", "harbor.models.trial.paths"]
    for name in modules:
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    class FakeCodex:
        _REMOTE_CODEX_HOME = PurePosixPath("/tmp/codex-home")
        _REMOTE_CODEX_SECRETS_DIR = PurePosixPath("/tmp/codex-secrets")
        def __init__(self, *a, **kw):
            self.commands = []
            self.fail = False
        async def exec_as_agent(self, environment, command, **kw):
            self.commands.append(command)
            if self.fail and command.startswith("setsid"):
                raise asyncio.CancelledError()
            return SimpleNamespace(return_code=0)
        exec_as_root = exec_as_agent
    sys.modules["harbor.agents.installed.codex"].Codex = FakeCodex
    sys.modules["harbor.environments.docker.docker"].DockerEnvironment = object
    for name, fields in {"JobConfig": ["n_concurrent_trials", "environment_build_timeout_multiplier"],
                         "AgentConfig": ["max_timeout_sec", "override_setup_timeout_sec"],
                         "EnvironmentConfig": ["override_cpus", "override_memory_mb"],
                         "VerifierConfig": ["max_timeout_sec"]}.items():
        target = "harbor.models.job.config" if name == "JobConfig" else "harbor.models.trial.config"
        setattr(sys.modules[target], name, type(name, (), {"model_fields": dict.fromkeys(fields)}))
    sys.modules["harbor.models.trial.paths"].EnvironmentPaths = SimpleNamespace(agent_dir=PurePosixPath("/logs/agent"))
    monkeypatch.delitem(sys.modules, "eval.phase3_codex_agent", raising=False)
    module = importlib.import_module("eval.phase3_codex_agent")
    yield module
    sys.modules.pop("eval.phase3_codex_agent", None)


def test_cancelled_agent_stops_group_then_collects_and_removes_auth(tmp_path, monkeypatch, agent_module):
    home = write_phase3_home(tmp_path / "config", "astra-luna")
    auth = tmp_path / "auth.json"
    auth.write_text("fake credential; no real authentication")
    monkeypatch.setenv("PHASE3A_AUTH_FILE", str(auth))
    uploaded = []
    async def upload_file(source, target):
        uploaded.append(target)
    environment = SimpleNamespace(upload_file=upload_file, default_user=None)
    agent = agent_module.Phase3Codex(phase3_home=home, version="0.123.0")
    agent.fail = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run("original instruction", environment, None))
    assert "/tmp/codex-home/AGENTS.md" in uploaded
    assert "/tmp/codex-home/agents/luna-max-worker.toml" in uploaded
    assert any("kill -TERM -- -$p" in cmd and "cp -R" in cmd for cmd in agent.commands)
    assert agent.commands[-1] == "rm -rf /tmp/codex-secrets /tmp/codex-home"
