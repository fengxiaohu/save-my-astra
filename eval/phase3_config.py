"""Static configuration and input freezing helpers for the Phase 3A pilot.

The regular profiles intentionally remain unchanged.  This module derives
experiment-only profiles at runtime and writes an isolated CODEX_HOME
fragment, so preparing a pilot run cannot alter the user's daily profile.
"""

from __future__ import annotations

import hashlib
from importlib import metadata
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from eval.paths import ROOT, TEMPLATES
from eval.profiles import Profile, load_profile
from eval.render import render_text


TASK_IDS = (
    "session-window-debug",
    "payments-pipeline-fix",
    "bun-sourcemap-leak",
    "nextjs-performance",
)

ARMS = ("astra-solo", "astra-luna")

_ARM_ALIASES = {
    "solo": "solo",
    "routed": "routed",
    "astra-solo": "solo",
    "astra-luna": "routed",
}

_PHASE3A_IDS = ROOT / "tasks" / "terminal-bench-4" / "phase3a_ids.txt"

_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def schedule() -> list[dict[str, str]]:
    """Return the fixed primary-run order from the Phase 3A protocol."""

    return [
        {"task": TASK_IDS[0], "arm": "astra-solo"},
        {"task": TASK_IDS[0], "arm": "astra-luna"},
        {"task": TASK_IDS[1], "arm": "astra-luna"},
        {"task": TASK_IDS[1], "arm": "astra-solo"},
        {"task": TASK_IDS[2], "arm": "astra-solo"},
        {"task": TASK_IDS[2], "arm": "astra-luna"},
        {"task": TASK_IDS[3], "arm": "astra-luna"},
        {"task": TASK_IDS[3], "arm": "astra-solo"},
    ]


def _canonical_arm(arm: str) -> str:
    try:
        return _ARM_ALIASES[arm]
    except (KeyError, TypeError):
        allowed = ", ".join(ARMS)
        raise ValueError(f"unknown Phase 3A arm {arm!r}; expected one of {allowed}") from None


def phase3_profile(arm: str) -> Profile:
    """Return an experiment-only profile with the parent effort set to medium.

    ``profiles/*.yaml`` are the daily configuration and are deliberately read
    without modification.  The routed arm is the Luna-only treatment; the
    solo arm has agents disabled and its worker fields are never rendered.
    """

    canonical = _canonical_arm(arm)
    if canonical == "solo":
        return replace(
            load_profile("astra-solo"),
            name="phase3a-solo",
            parent_effort="medium",
        )
    return replace(
        load_profile("astra-luna"),
        name="phase3a-routed",
        parent_effort="medium",
    )


def _phase3_config(profile: Profile, arm: str) -> str:
    """Render only fields understood by this repository's Codex templates."""

    if arm == "solo":
        return (
            f'model = "{profile.parent_model}"\n'
            f'model_reasoning_effort = "{profile.parent_effort}"\n\n'
            "[agents]\n"
            "enabled = false\n"
            "max_concurrent_threads_per_session = 1\n\n"
            "[features.multi_agent_v2]\n"
            "hide_spawn_agent_metadata = false\n"
        )
    return (
        f'model = "{profile.parent_model}"\n'
        f'model_reasoning_effort = "{profile.parent_effort}"\n\n'
        "[agents]\n"
        "enabled = true\n"
        f'default_subagent_model = "{profile.subagent_model}"\n'
        f'default_subagent_reasoning_effort = "{profile.subagent_effort}"\n'
        # Codex 0.153.4 counts child threads here; the root adds one slot.
        "max_concurrent_threads_per_session = 3\n\n"
        "[features.multi_agent_v2]\n"
        "hide_spawn_agent_metadata = false\n"
    )


def write_phase3_home(out: Path, arm: str) -> Path:
    """Write a self-contained Phase 3A CODEX_HOME fragment.

    The solo arm has no worker file.  The routed arm has exactly the Luna
    worker file generated from the existing worker template.  Both policies
    explicitly forbid workers from spawning descendants.
    """

    canonical = _canonical_arm(arm)
    profile = phase3_profile(canonical)
    dest = Path(out)
    if dest.is_symlink():
        raise ValueError(f"Phase 3A CODEX_HOME must not be a symlink: {dest}")
    if dest.exists():
        if not dest.is_dir():
            raise ValueError(f"Phase 3A CODEX_HOME is not a directory: {dest}")
        if any(dest.iterdir()):
            raise ValueError(f"Phase 3A CODEX_HOME must be empty: {dest}")
    else:
        dest.mkdir(parents=True, exist_ok=True)

    template_name = f"phase3a-{canonical}.md"
    policy = (TEMPLATES / template_name).read_text(encoding="utf-8")
    (dest / "AGENTS.md").write_text(
        render_text(policy, profile), encoding="utf-8"
    )
    (dest / "config.toml").write_text(
        _phase3_config(profile, canonical), encoding="utf-8"
    )

    if canonical == "routed":
        worker_dir = dest / "agents"
        worker_dir.mkdir(exist_ok=True)
        worker_template = (TEMPLATES / "agents" / "worker.toml").read_text(
            encoding="utf-8"
        )
        worker_name = f"{profile.subagent_name.replace('_', '-')}.toml"
        (worker_dir / worker_name).write_text(
            render_text(worker_template, profile), encoding="utf-8"
        )
    return out


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_checked(path: Path, root: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"missing {label}: {path}") from exc
    if not _inside(resolved, root):
        raise ValueError(f"{label} escapes task root through a symlink: {path}")
    return resolved


def _iter_tree(path: Path, root: Path, seen_dirs: set[Path]) -> Iterator[tuple[str, str, str]]:
    """Yield ``(relative path, kind, sha256-or-target)`` without following escapes."""

    resolved_dir = _resolve_checked(path, root, label="task path")
    if resolved_dir in seen_dirs:
        return
    seen_dirs.add(resolved_dir)

    entries = sorted(os.scandir(path), key=lambda entry: entry.name)
    for entry in entries:
        entry_path = Path(entry.path)
        rel = entry_path.relative_to(root).as_posix()
        if entry.is_symlink():
            target = _resolve_checked(entry_path, root, label=f"symlink {rel}")
            if target.is_dir():
                yield rel, "symlink-dir", target.relative_to(root).as_posix()
                yield from _iter_tree(entry_path, root, seen_dirs)
            elif target.is_file():
                yield rel, "symlink-file", _sha256_file(target)
            else:
                raise ValueError(f"unsupported symlink target: {entry_path}")
        elif entry.is_dir(follow_symlinks=False):
            yield from _iter_tree(entry_path, root, seen_dirs)
        elif entry.is_file(follow_symlinks=False):
            yield rel, "file", _sha256_file(entry_path)
        else:
            raise ValueError(f"unsupported filesystem entry: {entry_path}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(entries: list[tuple[str, str, str]]) -> str:
    digest = hashlib.sha256()
    for rel, kind, value in sorted(entries):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _timeout_field(data: dict[str, Any], section: str, key: str) -> int | float | None:
    section_data = data.get(section)
    if section_data is None:
        return None
    if not isinstance(section_data, dict):
        raise ValueError(f"task.toml [{section}] must be a table")
    if key not in section_data:
        return None
    value = section_data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"task.toml [{section}] {key} must be numeric")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"task.toml [{section}] {key} must be finite and positive")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _agent_timeout(data: dict[str, Any]) -> int | float | None:
    return _timeout_field(data, "agent", "timeout_sec")


def _verifier_timeout(data: dict[str, Any]) -> int | float | None:
    return _timeout_field(data, "verifier", "timeout_sec")


def _setup_timeout(data: dict[str, Any]) -> int | float | None:
    return _timeout_field(data, "environment", "build_timeout_sec")


def _source_files() -> list[Path]:
    files: set[Path] = set()
    files.update(
        path
        for path in (ROOT / "eval").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    files.update(
        path
        for path in (
            TEMPLATES / "phase3a-solo.md",
            TEMPLATES / "phase3a-routed.md",
            TEMPLATES / "config.snippet.toml",
            TEMPLATES / "agents" / "worker.toml",
            ROOT / "tasks" / "terminal-bench-4" / "phase3a_ids.txt",
            ROOT / "PHASE3A_PLAN.md",
            ROOT / "pyproject.toml",
            ROOT / "profiles" / "astra-solo.yaml",
            ROOT / "profiles" / "astra-luna.yaml",
        )
        if path.exists()
    )
    return sorted(files)


def _version_evidence(command: str) -> dict[str, Any]:
    executable = shutil.which(command)
    if executable is None:
        return {"command": command, "executable": None, "version": None}
    try:
        proc = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": command,
            "executable": executable,
            "version": None,
            "error": type(exc).__name__,
        }
    output = (proc.stdout or proc.stderr).strip()
    return {
        "command": command,
        "executable": executable,
        "version": output or None,
        "returncode": proc.returncode,
    }


def freeze_inputs(task_root: Path, codex_version: str) -> dict[str, Any]:
    """Validate and hash the four local task inputs without downloading or running them."""

    if not isinstance(codex_version, str) or not _SEMVER.fullmatch(codex_version.strip()):
        raise ValueError("codex_version must be an explicit semantic version")
    root = Path(task_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"task root is not a directory: {root}")

    directories = {
        child.name
        for child in root.iterdir()
        if child.is_dir() and not child.is_symlink()
    }
    expected = set(TASK_IDS)
    if directories != expected:
        missing = sorted(expected - directories)
        extra = sorted(directories - expected)
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise ValueError("task root must contain exactly Phase 3A task directories (" + ", ".join(detail) + ")")

    task_records: dict[str, dict[str, Any]] = {}
    for task_id in TASK_IDS:
        task_dir = root / task_id
        _resolve_checked(task_dir, root, label=f"task {task_id}")
        required = ("task.toml", "instruction.md", "environment", "tests")
        for name in required:
            _resolve_checked(task_dir / name, root, label=f"{task_id}/{name}")

        task_toml = task_dir / "task.toml"
        try:
            data = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"invalid {task_id}/task.toml: {exc}") from exc

        raw_agent_timeout = _agent_timeout(data)
        agent_timeout = (
            min(3600, raw_agent_timeout) if raw_agent_timeout is not None else 3600
        )

        entries = list(_iter_tree(task_dir, root, set()))
        task_records[task_id] = {
            "path": str(task_dir),
            "sha256": _tree_digest(entries),
            "sha256_tree": {
                rel: {"kind": kind, "sha256": value}
                for rel, kind, value in sorted(entries)
            },
            "agent_timeout_seconds": agent_timeout,
            "agent_timeout_seconds_raw": raw_agent_timeout,
            "verifier_timeout_seconds": _verifier_timeout(data),
            "setup_timeout_seconds": _setup_timeout(data),
        }

    source_hashes = {
        path.relative_to(ROOT).as_posix(): _sha256_file(path)
        for path in _source_files()
    }
    source_digest = _tree_digest(
        [(path, "file", digest) for path, digest in source_hashes.items()]
    )
    evidence = {
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
        },
        "harbor": _version_evidence("harbor"),
        "codex": {"version": codex_version.strip()},
    }
    return {
        "protocol": "phase3a",
        "task_root": str(root),
        "task_ids": list(TASK_IDS),
        "tasks": task_records,
        "source_sha256": source_hashes,
        "source_tree_sha256": source_digest,
        "versions": {name: value.get("version") for name, value in evidence.items()},
        "version_evidence": evidence,
        "dependency_versions": sorted(
            [dist.metadata.get("Name", "unknown"), dist.version]
            for dist in metadata.distributions()
        ),
    }
