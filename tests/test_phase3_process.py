import json
import os
import sys
import time

import pytest

from eval.phase3_process import (TelemetryError, quota_decision, read_telemetry,
                                 scrub_artifacts, supervise, temporary_chatgpt_auth,
                                 recover_auth_lease)


def sample(now=1000, used=10):
    return dict(observed_at=now, used_percent=used, resets_at=5000,
                limit_id="codex", resource_pressure="normal")


def test_quota_windows_and_soft_hard_limits():
    baseline = sample()
    assert quota_decision(sample(used=30), baseline, launching=True) == "quota_soft_stop"
    assert quota_decision(sample(used=30), baseline, launching=False) is None
    assert quota_decision(sample(used=40), baseline, launching=False) == "quota_hard_stop"
    assert quota_decision({**sample(), "resets_at": 6000}, baseline, launching=False) == "quota_window_changed"
    assert quota_decision(sample(used=9), baseline, launching=True) == "quota_reading_regressed"
    assert quota_decision({**sample(), "resource_pressure": "stop"}, baseline, launching=False) == "resource_stop"


@pytest.mark.parametrize("change", [{"observed_at": 800}, {"observed_at": 1001},
                                    {"used_percent": -1}, {"used_percent": True},
                                    {"used_percent": float("nan")}, {"resets_at": 999}])
def test_invalid_telemetry_blocks(tmp_path, change):
    file = tmp_path / "telemetry.json"
    file.write_text(json.dumps({**sample(), **change}))
    with pytest.raises(TelemetryError):
        read_telemetry(file, now=1000)


def test_temporary_auth_permissions_cleanup_and_redaction(tmp_path):
    source = tmp_path / "login.json"
    token = "fake-test-token-do-not-use"
    source.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": token}}))
    with pytest.raises(RuntimeError):
        with temporary_chatgpt_auth(source) as (path, secrets):
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            artifacts = tmp_path / "artifacts"
            artifacts.mkdir()
            (artifacts / "auth.json").write_text(source.read_text())
            (artifacts / "log.txt").write_text("token=" + token)
            scrub_artifacts(artifacts, secrets)
            assert not (artifacts / "auth.json").exists()
            assert token not in (artifacts / "log.txt").read_text()
            raise RuntimeError("simulated failure")
    assert not path.exists()
    assert token in source.read_text()


def test_api_auth_is_rejected(tmp_path):
    source = tmp_path / "auth.json"
    source.write_text(json.dumps({"OPENAI_API_KEY": "fake"}))
    with pytest.raises(ValueError, match="ChatGPT"):
        with temporary_chatgpt_auth(source):
            pytest.fail("API auth must not be used")


def test_supervisor_redacts_and_saves_output(tmp_path):
    result = supervise([sys.executable, "-c", "print('fake-sensitive-test-token')"],
                       cwd=tmp_path, env=os.environ.copy(), log=tmp_path / "raw.log",
                       timeout_seconds=5, secrets=["fake-sensitive-test-token"])
    assert result["returncode"] == 0
    assert (tmp_path / "raw.log").read_text() == "[REDACTED]\n"


def test_supervisor_stops_owned_process(tmp_path):
    started = time.monotonic()
    result = supervise([sys.executable, "-c", "import time; time.sleep(60)"],
                       cwd=tmp_path, env=os.environ.copy(), log=tmp_path / "raw.log",
                       timeout_seconds=.2)
    assert result["stop_reason"] == "outer_timeout"
    assert result["returncode"] != 0
    assert time.monotonic() - started < 5


def test_crash_lease_cleans_only_marked_private_directory(tmp_path, monkeypatch):
    from eval import phase3_process
    monkeypatch.setattr(phase3_process.tempfile, "gettempdir", lambda: str(tmp_path))
    def dead(*args):
        raise ProcessLookupError()
    monkeypatch.setattr(phase3_process.os, "kill", dead)
    directory = tmp_path / "phase3a-auth-test"
    directory.mkdir()
    lease = {"directory": str(directory), "pid": 12345, "nonce": "test"}
    (directory / "lease.json").write_text(json.dumps(lease))
    (directory / "auth.json").write_text("fake credential")
    pointer = tmp_path / "lease.json"
    pointer.write_text(json.dumps(lease))
    recover_auth_lease(pointer)
    assert not directory.exists()
    assert not pointer.exists()


def test_lease_cannot_remove_unmarked_directory(tmp_path, monkeypatch):
    from eval import phase3_process
    monkeypatch.setattr(phase3_process.tempfile, "gettempdir", lambda: str(tmp_path))
    directory = tmp_path / "phase3a-auth-unrelated"
    directory.mkdir()
    pointer = tmp_path / "lease.json"
    pointer.write_text(json.dumps({"directory": str(directory), "pid": 12345, "nonce": "test"}))
    with pytest.raises(ValueError, match="marker mismatch"):
        recover_auth_lease(pointer)
    assert directory.exists()
