import json
import os
import sys
import time

import pytest

from eval.phase3_process import (TelemetryError, quota_decision, read_telemetry,
                                 scrub_artifacts, supervise, temporary_chatgpt_auth,
                                 recover_auth_lease)
from eval.phase3 import is_quota_pause, resume_quota_pause


def sample(now=1000, used=10):
    return dict(observed_at=now, used_percent=used, resets_at=5000,
                limit_id="codex", resource_pressure="normal")


def weekly_sample(now=1000, used=10, weekly_used=10, weekly_reset=9000, primary_reset=5000):
    return {
        **sample(now=now, used=used),
        "resets_at": primary_reset,
        "schema_version": "phase3a.telemetry.v1",
        "weekly_used_percent": weekly_used,
        "weekly_resets_at": weekly_reset,
        "weekly_window_duration_mins": 10080,
    }


def test_quota_windows_and_soft_hard_limits():
    baseline = sample()
    assert quota_decision(sample(used=30), baseline, launching=True) == "quota_soft_stop"
    assert quota_decision(sample(used=30), baseline, launching=False) is None
    assert quota_decision(sample(used=40), baseline, launching=False) == "quota_hard_stop"
    assert quota_decision({**sample(), "resets_at": 6000}, baseline, launching=False) == "quota_window_changed"
    assert quota_decision(sample(used=9), baseline, launching=True) == "quota_reading_regressed"
    assert quota_decision({**sample(), "resource_pressure": "stop"}, baseline, launching=False) == "resource_stop"
    assert quota_decision(sample(used=100), baseline, launching=False) == "quota_exhausted"


def test_quota_exhaustion_is_absolute_even_when_delta_is_small():
    baseline = sample(used=99)
    assert quota_decision(sample(used=100), baseline, launching=True) == "quota_exhausted"
    assert quota_decision({**sample(used=100), "resets_at": 9000}, baseline, launching=True) == "quota_exhausted"


def test_weekly_exhaustion_stops_before_primary_and_keeps_primary_reading():
    baseline = weekly_sample(weekly_used=40)
    observed = weekly_sample(used=41, weekly_used=100)
    assert observed["used_percent"] == 41
    assert quota_decision(observed, baseline, launching=True) == "weekly_exhausted"
    assert quota_decision(sample(used=41), baseline, launching=True) == "weekly_telemetry_unavailable"


def test_observer_schema_requires_complete_weekly_window(tmp_path):
    file = tmp_path / "telemetry.json"
    file.write_text(json.dumps({**sample(), "schema_version": "phase3a.telemetry.v1"}))
    with pytest.raises(TelemetryError):
        read_telemetry(file, now=1000)

    # A legacy offline fixture without the observer schema remains primary-only.
    file.write_text(json.dumps(sample()))
    assert read_telemetry(file, now=1000)["used_percent"] == 10


def test_quota_pause_requires_a_new_usable_window():
    state = {"quota_pause": {"reason": "quota_hard_stop", "limit_id": "codex",
                              "resets_at": 5000, "used_percent": 40}}
    assert is_quota_pause("quota_hard_stop")
    assert resume_quota_pause(state, sample(now=2000, used=40)) == "quota_hard_stop"
    assert "quota_pause" in state
    # A forged future reset timestamp must not release the original pause
    # before an observation at or after the paused window's reset.
    early = {**sample(now=4999, used=1), "resets_at": 9000}
    assert resume_quota_pause(state, early) == "quota_hard_stop"
    assert "quota_pause" in state
    assert resume_quota_pause(state, {**sample(now=6000, used=100), "resets_at": 9000}) == "quota_exhausted"
    assert "quota_pause" in state
    assert resume_quota_pause(state, {**sample(now=10000, used=1), "resets_at": 11000}) is None
    assert "quota_pause" not in state
    assert state["quota_baseline"]["resets_at"] == 11000


def test_weekly_pause_waits_for_weekly_reset_not_primary_reset():
    state = {
        "quota_pause": {
            "reason": "weekly_exhausted",
            "window": "weekly",
            "limit_id": "codex",
            "resets_at": 9000,
            "used_percent": 100,
        }
    }
    primary_only_reset = weekly_sample(
        now=6000, used=1, weekly_used=100, weekly_reset=9000, primary_reset=7000
    )
    assert resume_quota_pause(state, primary_only_reset) == "weekly_exhausted"
    assert "quota_pause" in state
    resumed = weekly_sample(
        now=9000, used=1, weekly_used=1, weekly_reset=12000, primary_reset=10000
    )
    assert resume_quota_pause(state, resumed) is None
    assert state["quota_baseline"]["weekly_resets_at"] == 12000


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
