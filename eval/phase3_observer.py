"""Read-only Phase 3A quota and host-resource telemetry.

The observer never starts a Codex thread or model run.  It keeps one local
``codex app-server --stdio`` process alive, asks only for account metadata and
rate limits, and writes a small allow-listed snapshot atomically.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


PRIMARY_WINDOW_MINUTES = 300
WEEKLY_WINDOW_MINUTES = 7 * 24 * 60
WEEKLY_STOP_PERCENT = 100
MEMORY_FREE_STOP_PERCENT = 10
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_PROXY = "http://127.0.0.1:7890"
RPC_TIMEOUT_SECONDS = 25.0
RESOURCE_TIMEOUT_SECONDS = 5.0
TELEMETRY_SCHEMA = "phase3a.telemetry.v1"


class ObserverError(RuntimeError):
    """The observer could not produce a fresh, valid sample."""


class TelemetryUnavailable(ObserverError):
    """A required quota or host-resource source was missing or invalid."""


def _as_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelemetryUnavailable(f"{name} is not an integer")
    if minimum is not None and value < minimum:
        raise TelemetryUnavailable(f"{name} is below its minimum")
    return value


def _rpc_error_message(message: dict[str, Any]) -> str:
    error = message.get("error")
    if not isinstance(error, dict):
        return "app-server returned an error"
    # This is only used for an in-memory exception.  Raw JSON-RPC responses,
    # account metadata and credentials are never written to telemetry.
    text = error.get("message") or error.get("code") or "app-server returned an error"
    return str(text)[:240]


def _validate_chatgpt_auth(raw: bytes) -> None:
    try:
        data = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ObserverError("ChatGPT auth file is invalid") from exc
    tokens = data.get("tokens")
    if (
        data.get("auth_mode") not in (None, "chatgpt")
        or data.get("OPENAI_API_KEY")
        or not isinstance(tokens, dict)
        or not isinstance(tokens.get("access_token"), str)
        or not tokens.get("access_token")
    ):
        raise ObserverError("observer requires ChatGPT token authentication")


def _write_private_file(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _child_environment(private_home: Path, proxy: str | None) -> dict[str, str]:
    """Build app-server-only environment without API-key/provider overrides."""
    env = os.environ.copy()
    env["CODEX_HOME"] = str(private_home)
    for name in list(env):
        upper_name = name.upper()
        if upper_name.endswith("API_KEY") or upper_name in {
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "AZURE_OPENAI_ENDPOINT",
        }:
            env.pop(name, None)
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env.pop("ALL_PROXY", None)
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        env.pop("all_proxy", None)
    else:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
    return env


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


class AppServerRateLimits:
    """Long-lived, read-only app-server JSON-RPC client."""

    def __init__(
        self,
        auth_source: Path,
        *,
        proxy: str | None = DEFAULT_PROXY,
        rpc_timeout: float = RPC_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
        command: tuple[str, ...] = ("codex", "app-server", "--stdio"),
    ) -> None:
        self.auth_source = Path(auth_source)
        self.proxy = proxy
        self.rpc_timeout = rpc_timeout
        self.clock = clock
        self.command = command
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._private_home: Path | None = None
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1

    @property
    def process(self) -> subprocess.Popen[str] | None:
        return self._process

    @property
    def private_home(self) -> Path | None:
        return self._private_home

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        try:
            raw = self.auth_source.read_bytes()
        except OSError as exc:
            raise ObserverError("ChatGPT auth file is unavailable") from exc
        _validate_chatgpt_auth(raw)

        self._temporary = tempfile.TemporaryDirectory(prefix="phase3a-observer-")
        root = Path(self._temporary.name)
        root.chmod(0o700)
        private_home = root / "codex-home"
        private_home.mkdir(mode=0o700)
        secret_dir = root / "secret"
        secret_dir.mkdir(mode=0o700)
        secret_auth = secret_dir / "auth.json"
        _write_private_file(secret_auth, raw)
        # Keep the CODEX_HOME auth entry as a link, while pointing it at a
        # private copy so an app-server refresh cannot rewrite the source file.
        (private_home / "auth.json").symlink_to(secret_auth)
        self._private_home = private_home

        env = _child_environment(private_home, self.proxy)
        try:
            self._process = subprocess.Popen(
                list(self.command),
                cwd=str(root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            initialized = self._request(
                "initialize",
                {
                    "clientInfo": {"name": "phase3a-rate-limits-observer", "version": "0.1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            if not isinstance(initialized, dict):
                raise ObserverError("app-server initialize returned no result")
            self._notify("initialized", {})
            account = self._request("account/read", {})
            account_obj = account.get("account") if isinstance(account, dict) else None
            if not isinstance(account_obj, dict) or account_obj.get("type") != "chatgpt":
                raise ObserverError("app-server account is not ChatGPT")
            if account_obj.get("planType") != "plus":
                raise ObserverError("observer requires a ChatGPT Plus account")
        except BaseException:
            self.close()
            raise

    def _write(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise ObserverError("app-server is not running")
        try:
            self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise ObserverError("app-server stdin failed") from exc

    def _read_until(self, request_id: int) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise ObserverError("app-server is not running")
        deadline = time.monotonic() + self.rpc_timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._process.stdout], [], [], 0.5)
            if not ready:
                continue
            line = self._process.stdout.readline()
            if not line:
                raise ObserverError("app-server exited before its response")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON diagnostics are deliberately discarded.
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise ObserverError(_rpc_error_message(message))
                result = message.get("result")
                if not isinstance(result, dict):
                    raise ObserverError("app-server response has no object result")
                return result
            # account/rateLimits/updated is a sparse notification.  The next
            # full read remains authoritative; no raw notification is retained.
        raise ObserverError("app-server response timed out")

    def _request(self, method: str, params: Any) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self._read_until(request_id)

    def _notify(self, method: str, params: Any) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def read_rate_limits(self) -> tuple[dict[str, Any], float]:
        if self._process is None or self._process.poll() is not None:
            self.start()
        result = self._request("account/rateLimits/read", None)
        observed_at = self.clock()
        return result, observed_at

    def close(self) -> None:
        process, temporary = self._process, self._temporary
        self._process = None
        self._temporary = None
        self._private_home = None
        try:
            if process is not None:
                _terminate_process(process)
        finally:
            if temporary is not None:
                temporary.cleanup()

    def __enter__(self) -> "AppServerRateLimits":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _bucket_from_result(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        candidate = by_id.get("codex")
        if isinstance(candidate, dict):
            return str(candidate.get("limitId") or "codex"), candidate
        for key, value in by_id.items():
            if isinstance(value, dict) and value.get("limitId") == "codex":
                return "codex", value
    legacy = result.get("rateLimits")
    if isinstance(legacy, dict) and legacy.get("limitId") == "codex":
        return "codex", legacy
    raise TelemetryUnavailable("codex rate-limit bucket is missing")


def _window(bucket: dict[str, Any], name: str, duration: int) -> dict[str, Any]:
    candidate = bucket.get(name)
    if not isinstance(candidate, dict):
        raise TelemetryUnavailable(f"{name} rate-limit window is missing")
    actual_duration = _as_int(candidate.get("windowDurationMins"), f"{name}.windowDurationMins", minimum=1)
    if actual_duration != duration:
        raise TelemetryUnavailable(f"{name} window is not {duration} minutes")
    used = _as_int(candidate.get("usedPercent"), f"{name}.usedPercent", minimum=0)
    resets_at = _as_int(candidate.get("resetsAt"), f"{name}.resetsAt", minimum=1)
    if used > 100:
        raise TelemetryUnavailable(f"{name}.usedPercent is above 100")
    return {"used_percent": used, "window_duration_mins": actual_duration, "resets_at": resets_at}


def quota_snapshot(result: dict[str, Any], *, observed_at: float) -> dict[str, Any]:
    """Extract only the codex 5-hour and weekly fields from a rate-limit result."""
    if not isinstance(result, dict):
        raise TelemetryUnavailable("rate-limit result is not an object")
    limit_id, bucket = _bucket_from_result(result)
    primary = _window(bucket, "primary", PRIMARY_WINDOW_MINUTES)
    weekly = _window(bucket, "secondary", WEEKLY_WINDOW_MINUTES)
    if primary["resets_at"] <= observed_at:
        raise TelemetryUnavailable("primary quota window has already reset")
    if weekly["resets_at"] <= observed_at:
        raise TelemetryUnavailable("weekly quota window has already reset")
    reasons: list[str] = []
    if primary["used_percent"] >= 100:
        reasons.append("primary_exhausted")
    if weekly["used_percent"] >= WEEKLY_STOP_PERCENT:
        reasons.append("weekly_exhausted")
    return {
        "observed_at": observed_at,
        "quota_observed_at": observed_at,
        "used_percent": primary["used_percent"],
        "window_duration_mins": primary["window_duration_mins"],
        "resets_at": primary["resets_at"],
        "limit_id": limit_id,
        "weekly_used_percent": weekly["used_percent"],
        "weekly_window_duration_mins": weekly["window_duration_mins"],
        "weekly_resets_at": weekly["resets_at"],
        "quota_pause": bool(reasons),
        "quota_pause_reason": ",".join(reasons) if reasons else None,
    }


_FREE_PERCENT_RE = re.compile(r"System-wide memory free percentage:\s*(\d+)%")
_PAGE_SIZE_RE = re.compile(r"page size of\s+(\d+)\s+bytes", re.IGNORECASE)
_PAGES_RE = re.compile(r"^Pages\s+(.+?):\s+(\d+)\.?\s*$")


def _run_read_only(command: tuple[str, ...], *, timeout: float = RESOURCE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout, check=False)


def _parse_memory_pressure(text: str) -> int:
    match = _FREE_PERCENT_RE.search(text)
    if not match:
        raise TelemetryUnavailable("memory_pressure free percentage is missing")
    free_percent = int(match.group(1))
    if not 0 <= free_percent <= 100:
        raise TelemetryUnavailable("memory_pressure free percentage is invalid")
    return free_percent


def _parse_vm_stat(text: str) -> dict[str, int]:
    page_match = _PAGE_SIZE_RE.search(text)
    if not page_match:
        raise TelemetryUnavailable("vm_stat page size is missing")
    pages: dict[str, int] = {"page_size": int(page_match.group(1))}
    for line in text.splitlines():
        match = _PAGES_RE.match(line.strip())
        if not match:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().lower()).strip("_")
        pages[key] = int(match.group(2))
    if pages.get("page_size", 0) <= 0 or "free" not in pages:
        raise TelemetryUnavailable("vm_stat free pages are missing")
    return pages


def host_resources(
    *,
    command_runner: Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]] = _run_read_only,
) -> dict[str, Any]:
    """Read macOS memory pressure without inventing a normal state.

    Both read-only commands and the fields needed for the decision must be
    available.  Any missing command or unparsable output raises, so callers
    retain the previous file instead of writing a fresh but unsupported sample.
    """
    try:
        pressure = command_runner(("memory_pressure",))
        vm = command_runner(("vm_stat",))
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        raise TelemetryUnavailable("host resource command failed") from exc
    if pressure.returncode != 0 or vm.returncode != 0:
        raise TelemetryUnavailable("host resource command returned failure")
    free_percent = _parse_memory_pressure(pressure.stdout)
    pages = _parse_vm_stat(vm.stdout)
    is_stop = free_percent <= MEMORY_FREE_STOP_PERCENT
    return {
        "resource_pressure": "stop" if is_stop else "normal",
        "resource_stop_reason": "memory_free_percent_le_10" if is_stop else None,
        "memory_free_percent": free_percent,
        "vm_stat": {
            "page_size": pages["page_size"],
            "pages_free": pages["free"],
            "pages_pageouts": pages.get("pageouts"),
        },
    }


def collect_sample(
    client: AppServerRateLimits,
    *,
    resource_reader: Callable[[], dict[str, Any]] = host_resources,
) -> dict[str, Any]:
    result, observed_at = client.read_rate_limits()
    quota = quota_snapshot(result, observed_at=observed_at)
    try:
        resources = resource_reader()
    except ObserverError:
        raise
    except Exception as exc:
        raise TelemetryUnavailable("host resource reader failed") from exc
    if not isinstance(resources, dict) or resources.get("resource_pressure") not in {"normal", "stop"}:
        raise TelemetryUnavailable("host resource reader returned no valid pressure")
    sample = {"schema_version": TELEMETRY_SCHEMA, **quota, **resources}
    # An explicit allow-list prevents account, credits, raw RPC, or auth data
    # from crossing into the telemetry artifact.
    return {
        key: sample[key]
        for key in (
            "schema_version",
            "observed_at",
            "quota_observed_at",
            "used_percent",
            "window_duration_mins",
            "resets_at",
            "limit_id",
            "weekly_used_percent",
            "weekly_window_duration_mins",
            "weekly_resets_at",
            "quota_pause",
            "quota_pause_reason",
            "resource_pressure",
            "resource_stop_reason",
            "memory_free_percent",
            "vm_stat",
        )
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a validated, allow-listed sample without exposing partial JSON."""
    forbidden = {"account", "accountid", "credits", "raw", "tokens", "auth", "email"}

    def contains_forbidden(value: Any) -> bool:
        if isinstance(value, dict):
            if any(str(key).lower() in forbidden for key in value):
                return True
            return any(contains_forbidden(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_forbidden(item) for item in value)
        return False

    if contains_forbidden(payload):
        raise ValueError("telemetry payload contains forbidden account or credential data")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _term_unwinds() -> Iterator[None]:
    if threading.current_thread() is threading.main_thread():
        previous = signal.getsignal(signal.SIGTERM)

        def interrupted(signum, frame):
            raise KeyboardInterrupt("observer terminated")

        signal.signal(signal.SIGTERM, interrupted)
        try:
            yield
        finally:
            signal.signal(signal.SIGTERM, previous)
    else:
        yield


def run_observer(
    output: Path,
    auth_source: Path,
    *,
    proxy: str | None = DEFAULT_PROXY,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
    client_factory: Callable[..., AppServerRateLimits] = AppServerRateLimits,
    resource_reader: Callable[[], dict[str, Any]] = host_resources,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    if interval <= 0:
        raise ValueError("interval must be positive")
    client: AppServerRateLimits | None = None
    try:
        with _term_unwinds():
            while True:
                if client is None:
                    client = client_factory(Path(auth_source), proxy=proxy)
                try:
                    if client.process is None or client.process.poll() is not None:
                        client.start()
                    sample = collect_sample(client, resource_reader=resource_reader)
                    atomic_write_json(Path(output), sample)
                except ObserverError as exc:
                    print(f"telemetry unavailable: {type(exc).__name__}", file=sys.stderr)
                    client.close()
                    client = None
                    if once:
                        return 2
                if once:
                    return 0
                sleeper(interval)
    finally:
        if client is not None:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Phase 3A ChatGPT quota/resource telemetry")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--auth-source", default=Path.home() / ".codex" / "auth.json", type=Path)
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--interval", default=DEFAULT_INTERVAL_SECONDS, type=float)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_observer(
            args.output,
            args.auth_source,
            proxy=args.proxy or None,
            interval=args.interval,
            once=args.once,
        )
    except KeyboardInterrupt:
        return 130
    except (ObserverError, OSError, ValueError) as exc:
        print(f"observer failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
