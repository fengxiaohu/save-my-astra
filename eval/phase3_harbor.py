"""Harbor job adapter, based on the upstream JobConfig/TrialResult interfaces.

Harbor is optional until execution; importing this file never starts a process.
Source interfaces: laude-institute/harbor src/harbor/models/{job,trial}/config.py.
"""
from __future__ import annotations

import json
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}$")
LOCAL_IMAGE = re.compile(r"^sha256:[a-f0-9]{64}$")


def _validate_image(image: object, *, role: str, service: str) -> None:
    if not isinstance(image, str) or not (
        IMAGE.fullmatch(image) or LOCAL_IMAGE.fullmatch(image)
    ):
        raise ValueError(
            f"{role} service {service!r} must use an immutable registry digest "
            "or a local sha256 image ID"
        )


def _inspect_local_image(image: str) -> None:
    """Require a local image ID to resolve to exactly the frozen ID.

    Registry references are immutable by construction and may be pulled by
    Harbor.  A bare local image ID has no repository tag to anchor it, so Gate
    0 must prove that Docker currently owns that exact image object.
    """
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect local image {image}: {type(exc).__name__}") from exc
    if inspected.returncode != 0 or inspected.stdout.strip() != image:
        raise ValueError(f"local image ID is absent or mismatched: {image}")


def _validate_role(data: object, *, role: str) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("images"), dict):
        raise ValueError(f"Gate 0 requires a {role} environment with pinned images")
    images = data["images"]
    if "main" not in images:
        raise ValueError(f"Gate 0 requires a pinned {role} main image")
    for service, image in images.items():
        if not isinstance(service, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", service
        ):
            raise ValueError(f"{role} service names must be Docker Compose identifiers")
        _validate_image(image, role=role, service=service)
        if LOCAL_IMAGE.fullmatch(image):
            _inspect_local_image(image)
    for key in ("cpus", "memory_mb"):
        if type(data.get(key)) is not int or data[key] <= 0:
            raise ValueError(f"{role} environment {key} must be a positive integer")


def validate_environment(data: dict, *, require_verifier: bool = False) -> None:
    """Validate a legacy or role-separated frozen environment manifest.

    The legacy top-level shape remains readable for non-separate verifier
    tasks and old offline fixtures.  A separate verifier must use the explicit
    ``agent`` / ``verifier`` shape so its image and resource freeze cannot be
    silently inherited from the agent environment.
    """
    if not isinstance(data, dict):
        raise ValueError("Gate 0 environment must be an object")
    role_keys = {"agent", "verifier"} & set(data)
    if role_keys:
        if set(data) & {"images", "cpus", "memory_mb"}:
            raise ValueError("role-separated environment cannot mix legacy fields")
        if "agent" not in data:
            raise ValueError("role-separated environment is missing agent")
        _validate_role(data["agent"], role="agent")
        if "verifier" in data:
            _validate_role(data["verifier"], role="verifier")
        elif require_verifier:
            raise ValueError("separate verifier requires a frozen verifier environment")
        return

    _validate_role(data, role="agent")
    if require_verifier:
        raise ValueError("separate verifier requires role-separated environment data")


def _task_has_separate_verifier(task: dict) -> bool:
    verifier = task.get("verifier")
    if isinstance(verifier, dict) and (
        verifier.get("environment_mode") == "separate"
        or isinstance(verifier.get("environment"), dict)
    ):
        return True
    for step in task.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_verifier = step.get("verifier")
        if isinstance(step_verifier, dict) and (
            step_verifier.get("environment_mode") == "separate"
            or isinstance(step_verifier.get("environment"), dict)
        ):
            return True
    return False


def _role_environments(data: dict, *, separate_verifier: bool) -> tuple[dict, dict | None]:
    if "agent" in data:
        agent = data["agent"]
        verifier = data.get("verifier")
    else:
        agent = data
        verifier = None if separate_verifier else data
    if separate_verifier and verifier is None:
        raise ValueError("separate verifier requires role-separated environment data")
    return agent, verifier


def _definition_services(definition: Path, *, role: str) -> set[str]:
    """Return the services Harbor can start from one role's build context."""
    if not definition.is_dir():
        raise ValueError(f"{role} environment definition is missing: {definition}")
    compose = definition / "docker-compose.yaml"
    if not compose.exists():
        return {"main"}
    import yaml

    compose_data = yaml.safe_load(compose.read_text()) or {}
    services = compose_data.get("services", {})
    if not isinstance(services, dict):
        raise ValueError(f"{role} environment services must be a mapping")
    if compose_data.get("include") or any(
        isinstance(service, dict) and service.get("extends")
        for service in services.values()
    ):
        raise ValueError(
            f"{role} compose includes/extends require a resolved, audited environment snapshot"
        )
    return set(services) | {"main"}


def _verifier_definitions(task_dir: Path, task: dict) -> list[Path]:
    """Resolve the verifier build contexts used by Harbor's separate mode."""
    tests_dir = task_dir / "tests"
    steps = task.get("steps") or []
    if not steps:
        return [tests_dir]
    definitions = []
    for step in steps:
        name = step.get("name") if isinstance(step, dict) else None
        candidate = tests_dir / str(name) if name else tests_dir
        definitions.append(candidate if candidate.is_dir() else tests_dir)
    return definitions


def build_command(task_dir: Path, run_dir: Path, config_home: Path, codex_version: str,
                  agent_timeout_seconds: int) -> list[str]:
    task_dir, run_dir, config_home = task_dir.resolve(), run_dir.resolve(), config_home.resolve()
    native = tomllib.loads((config_home / "config.toml").read_text())
    if native.get("model") != "gpt-6-astra" or native.get("model_reasoning_effort") != "medium":
        raise ValueError("unexpected Phase 3A root configuration")
    environment = json.loads((run_dir / "environment.json").read_text())
    task = tomllib.loads((task_dir / "task.toml").read_text())
    separate_verifier = _task_has_separate_verifier(task)
    validate_environment(environment, require_verifier=separate_verifier)
    agent_environment, verifier_environment = _role_environments(
        environment, separate_verifier=separate_verifier
    )
    # A pinned overlay may not silently omit or add a service.  Harbor's
    # separate verifier is built from task/tests (not task/environment), so
    # validating both roles against the agent compose file would pin the
    # verifier to the wrong image set.
    agent_services = _definition_services(task_dir / "environment", role="agent")
    if set(agent_environment["images"]) != agent_services:
        raise ValueError("agent image freeze does not exactly match its environment definition")
    if separate_verifier:
        for definition in _verifier_definitions(task_dir, task):
            verifier_services = _definition_services(definition, role="verifier")
            if set(verifier_environment["images"]) != verifier_services:
                raise ValueError(
                    "verifier image freeze does not exactly match its environment definition"
                )

    if verifier_environment is not None and separate_verifier:
        task_verifier = task.get("verifier") or {}
        verifier_task_environment = task_verifier.get("environment") or task.get("environment") or {}
        for key in ("cpus", "memory_mb"):
            required = verifier_task_environment.get(key)
            if required is not None and verifier_environment[key] < required:
                raise ValueError(f"verifier {key} would lower the task-declared resource")
    build_timeout = task.get("environment", {}).get("build_timeout_sec", 600)
    job = {
        "job_name": "trial", "jobs_dir": str(run_dir / "harbor"), "n_attempts": 1,
        "n_concurrent_trials": 1, "retry": {"max_retries": 0},
        "tasks": [{"path": str(task_dir)}],
        "agents": [{"import_path": "eval.phase3_codex_agent:Phase3Codex", "model_name": "openai/gpt-6-astra",
                    "max_timeout_sec": min(agent_timeout_seconds, 3600), "override_setup_timeout_sec": 900,
                    "kwargs": {"version": codex_version, "config": str(config_home / "config.toml"),
                               "phase3_home": str(config_home)}}],
        "environment": {"import_path": "eval.phase3_codex_agent:PinnedDocker", "delete": True,
                        "force_build": False, "override_cpus": agent_environment["cpus"],
                        "override_memory_mb": agent_environment["memory_mb"],
                        "cpu_enforcement_policy": "limit", "memory_enforcement_policy": "limit",
                        "kwargs": {"phase3_owner_dir": str(run_dir),
                                   "phase3_images": agent_environment["images"],
                                   "phase3_verifier": verifier_environment}},
        "verifier": {"max_timeout_sec": 900},
        "environment_build_timeout_multiplier": min(1.0, 900 / build_timeout),
    }
    path = run_dir / "harbor-job.json"
    path.write_text(json.dumps(job, indent=2) + "\n")
    return ["harbor", "run", "--config", str(path)]


def _parent(source):
    if isinstance(source, dict):
        if isinstance(source.get("parent_thread_id"), str):
            return source["parent_thread_id"]
        for value in source.values():
            found = _parent(value)
            if found:
                return found
    return None


def session_events(agent_dir: Path) -> list[dict]:
    """Normalize native rollouts with file-level thread provenance, not prose."""
    sessions = []
    for file in sorted((agent_dir / "sessions").rglob("*.jsonl")):
        rows = [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
        metadata = next((row["payload"] for row in rows if row.get("type") == "session_meta"), None)
        if not metadata or not metadata.get("id"):
            raise ValueError("rollout lacks session metadata")
        sessions.append((metadata, rows, file.relative_to(agent_dir).as_posix()))
    calls = {}
    for _, rows, _ in sessions:
        for row in rows:
            body = row.get("payload", {})
            if body.get("type") == "collab_agent_spawn_end" and body.get("receiver_thread_id"):
                calls[body["receiver_thread_id"]] = body.get("call_id")
    events = []
    for meta, rows, source in sessions:
        tid, parent = meta["id"], _parent(meta.get("source"))
        if parent:
            events.append({"schema_version": "phase3a.events.v1", "event": "child_created",
                           "thread_id": tid, "parent_thread_id": parent, "status": "created",
                           "call_id": calls.get(tid) or "session:" + tid, "source_file": source})
        else:
            events.append({"type": "session_meta", "id": tid, "source_file": source})
        for index, row in enumerate(rows):
            kind, body = row.get("type"), row.get("payload", {})
            if not isinstance(body, dict):
                continue
            if kind == "turn_context":
                # This is the CLI's resolved per-turn context, not the spawn request.
                if isinstance(body.get("model"), str):
                    events.append({"type": "model_verification", "thread_id": tid,
                                   "child_thread_id": tid if parent else None,
                                   "model": body["model"], "reasoning_effort": body.get("effort") or body.get("reasoning_effort"),
                                   "source_file": source, "source_line": index + 1,
                                   "identity_basis": "codex_native_turn_context"})
            elif kind == "event_msg":
                events.append({**body, "thread_id": tid, "source_file": source,
                               "source_line": index + 1})
    marker = agent_dir / "phase3-collection.json"
    if marker.exists() and json.loads(marker.read_text()).get("complete") is True and sessions:
        events.append({"type": "collection_complete", "schema_version": "phase3a.events.v1",
                       "thread_ids": [meta["id"] for meta, _, _ in sessions]})
    return events


def _seconds(timing):
    if not isinstance(timing, dict) or not timing.get("started_at") or not timing.get("finished_at"):
        return None
    try:
        return (datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00")) -
                datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))).total_seconds()
    except (ValueError, TypeError):
        return None


def read_result(run_dir: Path) -> dict:
    candidates = []
    for file in (run_dir / "harbor").rglob("result.json"):
        payload = json.loads(file.read_text())
        if isinstance(payload, dict) and "trial_name" in payload and "task_name" in payload:
            candidates.append((file, payload))
    if len(candidates) != 1:
        return {"status": "observation_incomplete", "verifier": {"passed": None}, "events": [],
                "error": "expected exactly one Harbor trial result"}
    file, result = candidates[0]
    diff = file.parent / "agent" / "changes.diff"
    if diff.is_file():
        (run_dir / "changes.diff").write_bytes(diff.read_bytes())
    rewards = (result.get("verifier_result") or {}).get("rewards")
    # Standard Harbor binary reward. Other reward schemas require explicit adaptation.
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    passed = bool(reward) if type(reward) in (int, float) and reward in (0, 1) else None
    exception = (result.get("exception_info") or {}).get("exception_type")
    status = "completed"
    if exception in {"AgentTimeoutError", "AgentTimeoutException"}:
        status = "agent_timeout"
    elif exception in {"ModelNotFoundError", "AgentAuthenticationError", "ApiUsageLimitError"}:
        status = "model_unavailable"
    elif exception:
        status = "agent_error"
    if passed is None and status == "completed":
        status = "observation_incomplete"
    try:
        events = session_events(file.parent / "agent")
    except (OSError, ValueError, KeyError):
        events = []
        status = "observation_incomplete"
    return {"status": status, "verifier": {"passed": passed, "rewards": rewards}, "events": events,
            "exception_type": exception, "timing": {"agent_seconds": _seconds(result.get("agent_execution")),
            "setup_seconds": _seconds(result.get("environment_setup")), "verifier_seconds": _seconds(result.get("verifier"))}}


def cleanup_run(run_dir: Path) -> dict:
    """Remove only Docker objects with the compose project recorded by our environment."""
    owner = run_dir / "owned-projects.json"
    if not owner.exists():
        return {"complete": True, "projects": []}
    projects = json.loads(owner.read_text())
    errors = []
    for project in projects:
        if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,120}", project):
            raise ValueError("invalid owned Docker project")
        for kind, list_args, remove_args in (
            ("containers", ["ps", "-aq"], ["rm", "-f"]),
            ("volumes", ["volume", "ls", "-q"], ["volume", "rm"]),
            ("networks", ["network", "ls", "-q"], ["network", "rm"]),
        ):
            try:
                found = subprocess.run(["docker", *list_args, "--filter", f"label=com.docker.compose.project={project}"],
                                       capture_output=True, text=True, timeout=30, check=False)
                if found.returncode:
                    errors.append(kind + "_list_failed")
                    continue
                ids = found.stdout.split()
                if ids:
                    removed = subprocess.run(["docker", *remove_args, "--", *ids],
                                             capture_output=True, text=True, timeout=30, check=False)
                    if removed.returncode:
                        errors.append(kind + "_cleanup_failed")
            except (OSError, subprocess.TimeoutExpired):
                errors.append(kind + "_cleanup_unavailable")
    return {"complete": not errors, "projects": projects, "errors": errors}
