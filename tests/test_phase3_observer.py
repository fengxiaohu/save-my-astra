import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import phase3_observer as observer


def rate_result(*, primary_used=24, weekly_used=44, primary_duration=300, weekly_duration=10080):
    return {
        "accountId": "must-not-be-written",
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "planType": "plus",
                "credits": {"balance": "secret"},
                "primary": {
                    "usedPercent": primary_used,
                    "windowDurationMins": primary_duration,
                    "resetsAt": 5000,
                },
                "secondary": {
                    "usedPercent": weekly_used,
                    "windowDurationMins": weekly_duration,
                    "resetsAt": 9000,
                },
            }
        },
    }


def resources(*, free_percent=53, pressure=None):
    return {
        "resource_pressure": pressure or ("stop" if free_percent <= 10 else "normal"),
        "resource_stop_reason": "memory_free_percent_le_10" if free_percent <= 10 else None,
        "memory_free_percent": free_percent,
        "vm_stat": {"page_size": 16384, "pages_free": 100, "pages_pageouts": 2},
    }


class FakeClient:
    def __init__(self, result, observed_at=1234.5):
        self.result = result
        self.observed_at = observed_at
        self.process = SimpleNamespace(poll=lambda: None)
        self.closed = False

    def start(self):
        return None

    def read_rate_limits(self):
        return self.result, self.observed_at

    def close(self):
        self.closed = True


def completed(stdout, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_quota_snapshot_requires_300_minute_and_weekly_windows():
    sample = observer.quota_snapshot(rate_result(), observed_at=1000.0)
    assert sample["observed_at"] == 1000.0
    assert sample["quota_observed_at"] == 1000.0
    assert sample["used_percent"] == 24
    assert sample["window_duration_mins"] == 300
    assert sample["weekly_window_duration_mins"] == 10080
    assert sample["limit_id"] == "codex"

    with pytest.raises(observer.TelemetryUnavailable):
        observer.quota_snapshot(rate_result(primary_duration=60), observed_at=1000.0)
    with pytest.raises(observer.TelemetryUnavailable):
        observer.quota_snapshot(rate_result(weekly_duration=1440), observed_at=1000.0)


def test_weekly_exhaustion_pauses_without_faking_primary_usage():
    sample = observer.quota_snapshot(
        rate_result(primary_used=41, weekly_used=100), observed_at=1000.0
    )
    assert sample["used_percent"] == 41
    assert sample["quota_pause"] is True
    assert sample["quota_pause_reason"] == "weekly_exhausted"
    assert sample["resets_at"] == 5000
    assert sample["weekly_resets_at"] == 9000


def test_host_resources_uses_explicit_threshold_and_requires_both_sources():
    outputs = {
        ("memory_pressure",): completed("System-wide memory free percentage: 11%\n"),
        ("vm_stat",): completed(
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free: 100.\nPages pageouts: 2.\n"
        ),
    }
    assert observer.host_resources(command_runner=lambda command: outputs[command])["resource_pressure"] == "normal"

    outputs[("memory_pressure",)] = completed("System-wide memory free percentage: 10%\n")
    stopped = observer.host_resources(command_runner=lambda command: outputs[command])
    assert stopped["resource_pressure"] == "stop"
    assert stopped["resource_stop_reason"] == "memory_free_percent_le_10"

    outputs[("memory_pressure",)] = completed("unparseable")
    with pytest.raises(observer.TelemetryUnavailable):
        observer.host_resources(command_runner=lambda command: outputs[command])


def test_collect_sample_allowlists_fields_and_preserves_quota_time():
    client = FakeClient(rate_result(), observed_at=2345.25)
    sample = observer.collect_sample(client, resource_reader=lambda: resources())
    serialized = json.dumps(sample)
    assert sample["observed_at"] == 2345.25
    assert sample["quota_observed_at"] == 2345.25
    assert "accountId" not in serialized
    assert "credits" not in serialized
    assert "must-not-be-written" not in serialized


def test_missing_resource_does_not_write_new_sample(tmp_path: Path):
    output = tmp_path / "telemetry.json"
    previous = {"observed_at": 1000, "used_percent": 20, "resets_at": 5000}
    output.write_text(json.dumps(previous) + "\n")
    client = FakeClient(rate_result(), observed_at=2000.0)

    with pytest.raises(observer.TelemetryUnavailable):
        observer.collect_sample(client, resource_reader=lambda: (_ for _ in ()).throw(
            observer.TelemetryUnavailable("missing memory source")
        ))
    assert json.loads(output.read_text()) == previous


def test_run_observer_once_writes_sample_and_closes_client(tmp_path: Path):
    output = tmp_path / "telemetry.json"
    clients = []

    def factory(*args, **kwargs):
        client = FakeClient(rate_result(), observed_at=3000.0)
        clients.append(client)
        return client

    assert observer.run_observer(
        output,
        tmp_path / "auth.json",
        once=True,
        client_factory=factory,
        resource_reader=lambda: resources(),
    ) == 0
    assert json.loads(output.read_text())["observed_at"] == 3000.0
    assert clients[0].closed is True


def test_atomic_write_rejects_private_fields_and_leaves_no_temp_file(tmp_path: Path):
    output = tmp_path / "telemetry.json"
    with pytest.raises(ValueError, match="forbidden"):
        observer.atomic_write_json(output, {"account": {"email": "private"}})
    assert not output.exists()
    assert list(tmp_path.glob("*.tmp")) == []

    observer.atomic_write_json(output, {"observed_at": 1, "resource_pressure": "normal"})
    assert json.loads(output.read_text())["observed_at"] == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_child_proxy_and_auth_environment_are_scoped(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ALL_PROXY", "http://wrong")
    env = observer._child_environment(tmp_path, "http://127.0.0.1:7890")
    assert env["CODEX_HOME"] == str(tmp_path)
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert "ALL_PROXY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env.get("HOME") == str(Path.home())


def test_host_resource_command_failure_is_not_normal():
    def failed(command):
        return completed("", returncode=1)

    with pytest.raises(observer.TelemetryUnavailable):
        observer.host_resources(command_runner=failed)
