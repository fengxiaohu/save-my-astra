"""Local supervision only; no model or container is launched on import."""
from __future__ import annotations

import json
import math
import os
import queue
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class TelemetryError(ValueError):
    pass


_WEEKLY_KEYS = (
    "weekly_used_percent",
    "weekly_resets_at",
    "weekly_window_duration_mins",
)
_WEEKLY_SCHEMA = "phase3a.telemetry.v1"
_WEEKLY_WINDOW_MINUTES = 7 * 24 * 60


def read_telemetry(
    path: Path, *, now: float | None = None, require_weekly: bool | None = None
) -> dict:
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text())
        for key in ("observed_at", "used_percent", "resets_at"):
            if isinstance(data[key], bool) or not isinstance(data[key], (int, float)) or not math.isfinite(data[key]):
                raise ValueError(f"invalid {key}")
        if not 0 <= now - data["observed_at"] <= 120:
            raise ValueError("telemetry stale or from the future")
        if not 0 <= data["used_percent"] <= 100 or data["resets_at"] <= now:
            raise ValueError("invalid quota window")
        if not isinstance(data.get("limit_id"), str) or not data["limit_id"]:
            raise ValueError("missing limit ID")
        if data.get("resource_pressure") not in {"normal", "stop"}:
            raise ValueError("resource_pressure must be normal or stop")
        # Observer samples advertise the schema and must carry both windows.
        # Legacy offline fixtures omit the schema and remain valid primary-only
        # samples; they never receive an invented weekly value.
        if require_weekly is None:
            require_weekly = data.get("schema_version") == _WEEKLY_SCHEMA
        present_weekly = [key in data for key in _WEEKLY_KEYS]
        if require_weekly or any(present_weekly):
            if not all(present_weekly):
                raise ValueError("incomplete weekly telemetry")
            weekly_used = data["weekly_used_percent"]
            weekly_resets = data["weekly_resets_at"]
            weekly_duration = data["weekly_window_duration_mins"]
            for value, name in (
                (weekly_used, "weekly_used_percent"),
                (weekly_resets, "weekly_resets_at"),
                (weekly_duration, "weekly_window_duration_mins"),
            ):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"invalid {name}")
            if not 0 <= weekly_used <= 100 or weekly_resets <= now:
                raise ValueError("invalid weekly quota window")
            if weekly_duration != _WEEKLY_WINDOW_MINUTES:
                raise ValueError("weekly quota window has unexpected duration")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TelemetryError(f"telemetry unavailable: {type(exc).__name__}") from None
    return data


def quota_decision(sample: dict, baseline: dict, *, launching: bool) -> str | None:
    if sample["limit_id"] != baseline["limit_id"]:
        return "quota_limit_changed"
    if sample["resource_pressure"] == "stop":
        return "resource_stop"
    weekly_present = [key in sample for key in _WEEKLY_KEYS]
    baseline_weekly_present = [key in baseline for key in _WEEKLY_KEYS]
    if any(weekly_present) or any(baseline_weekly_present):
        if not all(weekly_present) or not all(baseline_weekly_present):
            return "weekly_telemetry_unavailable"
        # Weekly exhaustion must win over the five-hour signal. Otherwise a
        # reset of the primary bucket could incorrectly release a weekly stop.
        if sample["weekly_used_percent"] >= 100:
            return "weekly_exhausted"
    # A full five-hour bucket is a hard stop even when the percentage delta
    # from the frozen baseline is below the 30 point threshold.
    if sample["used_percent"] >= 100:
        return "quota_exhausted"
    if sample["resets_at"] != baseline["resets_at"]:
        return "quota_window_changed"
    if any(weekly_present) and sample["weekly_resets_at"] != baseline["weekly_resets_at"]:
        return "weekly_window_changed"
    delta = sample["used_percent"] - baseline["used_percent"]
    if delta < 0:
        return "quota_reading_regressed"
    if any(weekly_present) and sample["weekly_used_percent"] < baseline["weekly_used_percent"]:
        return "quota_reading_regressed"
    if delta >= 30:
        return "quota_hard_stop"
    if launching and delta >= 20:
        return "quota_soft_stop"
    return None


@contextmanager
def interruption_handlers():
    """Turn SIGTERM into stack unwinding so process and credential cleanup run."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt("runner terminated")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def recover_auth_lease(lease_file: Path) -> None:
    """Clean only this runner's marked private directory after an uncatchable crash."""
    if not lease_file.exists():
        return
    lease = json.loads(lease_file.read_text())
    directory = Path(lease["directory"])
    if directory.exists():
        if (directory.is_symlink() or directory.parent.resolve() != Path(tempfile.gettempdir()).resolve()
                or not directory.name.startswith("phase3a-auth-") or directory.stat().st_uid != os.getuid()):
            raise ValueError("invalid credential cleanup lease")
        marker = directory / "lease.json"
        if not marker.exists() or json.loads(marker.read_text()) != lease:
            raise ValueError("credential cleanup lease marker mismatch")
        try:
            os.kill(lease["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError("credential lease owner is still alive")
        shutil.rmtree(directory)
    lease_file.unlink()


@contextmanager
def temporary_chatgpt_auth(source: Path, *, lease_file: Path | None = None):
    """Copy to a private temporary directory outside artifacts; never expose values."""
    try:
        raw = source.read_text()
        data = json.loads(raw)
    except (OSError, ValueError):
        raise ValueError("ChatGPT auth file unavailable or invalid") from None
    tokens = data.get("tokens")
    if data.get("auth_mode") not in (None, "chatgpt") or data.get("OPENAI_API_KEY") or not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise ValueError("Phase 3A requires ChatGPT token authentication")
    secrets = [v for v in tokens.values() if isinstance(v, str) and len(v) > 12]
    with tempfile.TemporaryDirectory(prefix="phase3a-auth-") as tmp:
        home = Path(tmp)
        home.chmod(0o700)
        destination = home / "auth.json"
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(raw)
        if lease_file is not None:
            lease = {"directory": str(home), "pid": os.getpid(), "nonce": uuid.uuid4().hex}
            (home / "lease.json").write_text(json.dumps(lease))
            lease_file.write_text(json.dumps(lease))
        try:
            yield destination, secrets
        finally:
            destination.unlink(missing_ok=True)
            if lease_file is not None:
                lease_file.unlink(missing_ok=True)


def scrub_artifacts(root: Path, secrets: list[str]) -> None:
    """Remove accidental auth files and redact exact credential values in text logs."""
    for path in root.rglob("*"):
        if path.is_symlink():
            path.unlink()
            continue
        if not path.is_file():
            continue
        if path.name == "auth.json":
            path.unlink()
            continue
        content = path.read_bytes()
        clean = content
        for secret in secrets:
            clean = clean.replace(secret.encode(), b"[REDACTED]")
        if clean != content:
            path.write_bytes(clean)


def supervise(command: list[str], *, cwd: Path, env: dict, log: Path,
              timeout_seconds: float, stop_check=None, secrets: list[str] | None = None) -> dict:
    """Stream redacted output and terminate the owned process group on interruption."""
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace",
                            start_new_session=True)
    messages: queue.Queue = queue.Queue()

    def pump():
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    reason = None
    eof = False
    exited_at = None

    def terminate():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)

    try:
        with log.open("w") as stream:
            while not eof or proc.poll() is None:
                if proc.poll() is not None and not eof:
                    exited_at = exited_at or time.monotonic()
                    if time.monotonic() - exited_at > 2:
                        # A descendant must not keep the stdout pipe open forever.
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        reason = reason or "descendant_cleanup"
                        break
                if proc.poll() is None and reason is None:
                    if time.monotonic() - started >= timeout_seconds:
                        reason = "outer_timeout"
                    elif stop_check is not None:
                        reason = stop_check()
                    if reason:
                        terminate()
                try:
                    line = messages.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    eof = True
                else:
                    for secret in secrets or []:
                        line = line.replace(secret, "[REDACTED]")
                    stream.write(line)
                    stream.flush()
    except BaseException:
        terminate()
        raise
    finally:
        if proc.poll() is None:
            terminate()
        reader.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
    return {"returncode": proc.wait(), "stop_reason": reason,
            "end_to_end_seconds": time.monotonic() - started}
