"""Harbor-loaded classes. Imported only by the explicitly invoked Harbor job.

Uses the upstream installed Codex setup and Docker environment APIs. No inference
client is created here; the original task instruction and verifier remain Harbor's.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import shlex
import tempfile
from pathlib import Path

from harbor.agents.installed.codex import Codex
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, VerifierConfig
from harbor.models.trial.paths import EnvironmentPaths


_LOCAL_IMAGE = re.compile(r"^sha256:[a-f0-9]{64}$")


def _record_owned_project(owner: Path, project: str) -> None:
    """Atomically append one compose project to the run's ownership ledger."""
    ledger = owner / "owned-projects.json"
    lock_path = owner / "owned-projects.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if ledger.exists():
                try:
                    existing = json.loads(ledger.read_text())
                except (OSError, ValueError) as exc:
                    raise ValueError("owned Docker project ledger is invalid") from exc
                if not isinstance(existing, list) or not all(
                    isinstance(item, str) for item in existing
                ):
                    raise ValueError("owned Docker project ledger is invalid")
            else:
                existing = []
            projects = list(dict.fromkeys([*existing, project]))
            fd, temporary = tempfile.mkstemp(prefix=".owned-projects.", dir=owner)
            os.close(fd)
            temporary_path = Path(temporary)
            try:
                temporary_path.write_text(json.dumps(projects) + "\n")
                os.replace(temporary_path, ledger)
            finally:
                temporary_path.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _is_verifier_session(session: str) -> bool:
    """Match Harbor 0.22's separate-verifier session naming contract."""
    return "__verifier__" in session


def _role_environment(
    *, session: str, agent_images: dict, verifier: dict | None
) -> tuple[str, dict, int | None, int | None]:
    verifier_session = _is_verifier_session(session)
    if verifier_session:
        if not isinstance(verifier, dict):
            raise ValueError("separate verifier environment is not frozen")
        images = verifier.get("images")
        role = "verifier"
        cpus = verifier.get("cpus")
        memory_mb = verifier.get("memory_mb")
    elif session.endswith("__env"):
        images = agent_images
        role = "agent"
        cpus = None
        memory_mb = None
    else:
        raise ValueError(f"unrecognized Harbor environment role session: {session}")
    if not isinstance(images, dict) or not isinstance(images.get("main"), str):
        raise ValueError(f"{role} environment is missing a pinned main image")
    if cpus is not None and (type(cpus) is not int or cpus <= 0):
        raise ValueError(f"{role} environment cpus must be a positive integer")
    if memory_mb is not None and (type(memory_mb) is not int or memory_mb <= 0):
        raise ValueError(f"{role} environment memory_mb must be a positive integer")
    if role == "verifier" and (cpus is None or memory_mb is None):
        raise ValueError("separate verifier environment is missing frozen resources")
    return role, images, cpus, memory_mb


for cls, names in (
    (JobConfig, {"n_concurrent_trials", "environment_build_timeout_multiplier"}),
    (AgentConfig, {"max_timeout_sec", "override_setup_timeout_sec"}),
    (EnvironmentConfig, {"override_cpus", "override_memory_mb"}),
    (VerifierConfig, {"max_timeout_sec"}),
):
    if not names <= set(cls.model_fields):
        raise RuntimeError("Installed Harbor lacks required Phase 3A timeout/resource interfaces")


class PinnedDocker(DockerEnvironment):
    def __init__(self, *args, phase3_owner_dir, phase3_images, phase3_verifier=None, **kwargs):
        owner = Path(phase3_owner_dir)
        owner.mkdir(parents=True, exist_ok=True)
        session = kwargs.get("session_id")
        if not isinstance(session, str):
            raise ValueError("Harbor must supply an isolated session ID")
        project = session.lower()
        if not re.match(r"^[a-z0-9]", project):
            project = "0" + project
        project = re.sub(r"[^a-z0-9_-]", "-", project)
        _record_owned_project(owner, project)
        task_env = kwargs["task_env_config"]
        role, images, role_cpus, role_memory_mb = _role_environment(
            session=session, agent_images=phase3_images, verifier=phase3_verifier
        )
        if role == "verifier":
            declared_cpus = task_env.cpus
            declared_memory_mb = task_env.memory_mb
            if declared_cpus is not None and role_cpus < declared_cpus:
                raise ValueError("frozen verifier cpus would lower task resources")
            if declared_memory_mb is not None and role_memory_mb < declared_memory_mb:
                raise ValueError("frozen verifier memory_mb would lower task resources")
            kwargs["override_cpus"] = role_cpus
            kwargs["override_memory_mb"] = role_memory_mb
        # Use a prebuilt immutable main image, avoiding a fresh floating-tag build.
        kwargs["task_env_config"] = task_env.model_copy(update={"docker_image": images["main"]})
        # Keep role overlays separate: Harbor starts the separate verifier
        # while the agent environment is still alive, and the agent's later
        # ``down`` must keep referring to its own image definition.
        overlay = owner / f"pinned-compose-{role}.yaml"
        lines = ["services:"]
        for name, image in images.items():
            lines.extend([f"  {name}:", "    build: !reset null", f"    image: {json.dumps(image)}"])
            if _LOCAL_IMAGE.fullmatch(image):
                lines.append("    pull_policy: never")
        overlay.write_text("\n".join(lines) + "\n")
        kwargs["extra_docker_compose"] = [*kwargs.get("extra_docker_compose", []), overlay]
        super().__init__(*args, **kwargs)


class Phase3Codex(Codex):
    def __init__(self, *args, phase3_home, **kwargs):
        self.phase3_home = Path(phase3_home)
        if not kwargs.get("version"):
            raise ValueError("An exact Codex version is required")
        super().__init__(*args, **kwargs)

    @staticmethod
    def name():
        return "phase3a-codex"

    def populate_context_post_run(self, context):
        # Phase 3A consumes native rollouts and does not request API price estimates.
        return None

    async def run(self, instruction, environment, context):
        auth = Path(os.environ.get("PHASE3A_AUTH_FILE", ""))
        if not auth.is_file():
            raise ValueError("Phase 3A ChatGPT authentication was not staged")
        home = self._REMOTE_CODEX_HOME.as_posix()
        secrets_dir = self._REMOTE_CODEX_SECRETS_DIR.as_posix()
        logs = EnvironmentPaths.agent_dir.as_posix()
        env = {"CODEX_HOME": home}
        result_code = None

        async def command(text, *, root=False):
            fn = self.exec_as_root if root else self.exec_as_agent
            result = await fn(environment, command=text, env=env)
            if result.return_code != 0:
                raise RuntimeError("Phase 3A container setup or collection failed")
            return result

        await command(f"mkdir -p {shlex.quote(home)} {shlex.quote(secrets_dir)} {shlex.quote(logs)}")
        try:
            await environment.upload_file(auth, secrets_dir + "/auth.json")
            for file in self.phase3_home.rglob("*"):
                if file.is_file() and not file.is_symlink():
                    relative = file.relative_to(self.phase3_home).as_posix()
                    await command("mkdir -p " + shlex.quote(str(Path(home, relative).parent)))
                    await environment.upload_file(file, home + "/" + relative)
            if environment.default_user is not None:
                await command(f"chown -R {shlex.quote(str(environment.default_user))} {shlex.quote(home)} {shlex.quote(secrets_dir)}", root=True)
            await command(f"chmod 700 {shlex.quote(home)} {shlex.quote(secrets_dir)}; chmod 600 {shlex.quote(secrets_dir + '/auth.json')}; ln -s {shlex.quote(secrets_dir + '/auth.json')} {shlex.quote(home + '/auth.json')}")
            # Isolated trial container is the sandbox; upstream Harbor uses these flags.
            invocation = ("if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                          # This runs inside the new session.  ``$$`` is therefore
                          # the process-group leader, before ``exec`` replaces the
                          # shell with Codex; no host-side ``ps`` race is needed.
                          f"printf '%s\\n' \"$$\" > {shlex.quote(home + '/agent.pgid')}; "
                          "exec codex exec --dangerously-bypass-approvals-and-sandbox "
                          "--skip-git-repo-check --model gpt-6-astra --json --enable unified_exec -- "
                          + shlex.quote(instruction) + " >" + shlex.quote(logs + "/codex.txt") + " 2>&1")
            # ``setsid bash -c`` makes the inner shell (then Codex after ``exec``)
            # the leader of a fresh, run-owned process group.  Keep that shell
            # inside the session so cleanup never targets the Harbor exec shell.
            pid_path = shlex.quote(home + "/agent.pid")
            script = (
                # Keep the shell inside the new session so it can record its own
                # ``$$`` before ``exec codex``.  Recording the background PID is
                # still useful evidence, but cleanup targets the verified PGID.
                f"setsid bash -c {shlex.quote(invocation)} & p=$!; "
                f"printf '%s\\n' \"$p\" > {pid_path}; "
                "wait $p"
            )
            result = await self.exec_as_agent(environment, command=script, env=env)
            result_code = result.return_code
            if result_code:
                raise RuntimeError("Codex exited unsuccessfully; inspect redacted trial logs")
        finally:
            # Timeout cancellation must stop the container process before copying logs.
            cleanup = (
                "set -e; "
                f"if [ -f {shlex.quote(home + '/agent.pgid')} ]; then "
                f"g=$(cat {shlex.quote(home + '/agent.pgid')}); "
                # Reject malformed or zero PGIDs so a corrupt marker can never
                # turn into a broad process kill.  The group was created by our
                # ``setsid bash -c`` launch above and is the only kill target.
                "case $g in ''|*[!0-9]*|0) ;; "
                "*) kill -TERM -- -$g 2>/dev/null || true; sleep 1; "
                "kill -KILL -- -$g 2>/dev/null || true;; esac; fi; "
                f"mkdir -p {shlex.quote(logs)}; "
                f"if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git diff --binary HEAD > {shlex.quote(logs + '/changes.diff')} || printf '%s' 'Unavailable: Git diff failed' > {shlex.quote(logs + '/changes.diff')}; "
                f"git ls-files --others --exclude-standard > {shlex.quote(logs + '/untracked-files.txt')} || true; "
                f"else printf '%s' 'Unavailable: task workspace is not a Git checkout' > {shlex.quote(logs + '/changes.diff')}; fi; "
                f"if [ -d {shlex.quote(home + '/sessions')} ]; then cp -R {shlex.quote(home + '/sessions')} {shlex.quote(logs + '/sessions')}; "
                f"printf '%s' '{{\"complete\":true}}' > {shlex.quote(logs + '/phase3-collection.json')}; fi"
            )
            try:
                await asyncio.wait_for(command(cleanup), timeout=30)
            finally:
                await asyncio.wait_for(command(f"rm -rf {shlex.quote(secrets_dir)} {shlex.quote(home)}"), timeout=15)
