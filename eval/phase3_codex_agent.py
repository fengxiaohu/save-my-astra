"""Harbor-loaded classes. Imported only by the explicitly invoked Harbor job.

Uses the upstream installed Codex setup and Docker environment APIs. No inference
client is created here; the original task instruction and verifier remain Harbor's.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from pathlib import Path

from harbor.agents.installed.codex import Codex
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, VerifierConfig
from harbor.models.trial.paths import EnvironmentPaths


for cls, names in (
    (JobConfig, {"n_concurrent_trials", "environment_build_timeout_multiplier"}),
    (AgentConfig, {"max_timeout_sec", "override_setup_timeout_sec"}),
    (EnvironmentConfig, {"override_cpus", "override_memory_mb"}),
    (VerifierConfig, {"max_timeout_sec"}),
):
    if not names <= set(cls.model_fields):
        raise RuntimeError("Installed Harbor lacks required Phase 3A timeout/resource interfaces")


class PinnedDocker(DockerEnvironment):
    def __init__(self, *args, phase3_owner_dir, phase3_images, **kwargs):
        owner = Path(phase3_owner_dir)
        owner.mkdir(parents=True, exist_ok=True)
        session = kwargs.get("session_id")
        if not isinstance(session, str):
            raise ValueError("Harbor must supply an isolated session ID")
        project = session.lower()
        if not re.match(r"^[a-z0-9]", project):
            project = "0" + project
        project = re.sub(r"[^a-z0-9_-]", "-", project)
        (owner / "owned-projects.json").write_text(json.dumps([project]))
        task_env = kwargs["task_env_config"]
        # Use a prebuilt immutable main image, avoiding a fresh floating-tag build.
        kwargs["task_env_config"] = task_env.model_copy(update={"docker_image": phase3_images["main"]})
        overlay = owner / "pinned-compose.yaml"
        lines = ["services:"]
        for name, image in phase3_images.items():
            lines.extend([f"  {name}:", "    build: !reset null", f"    image: {json.dumps(image)}"])
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
                          "set -o pipefail; setsid codex exec --dangerously-bypass-approvals-and-sandbox "
                          "--skip-git-repo-check --model gpt-6-astra --json --enable unified_exec -- "
                          + shlex.quote(instruction) + " >" + shlex.quote(logs + "/codex.txt") + " 2>&1")
            # Launch an owned process group. Record its PID before waiting for it.
            launch = ("bash -c " + shlex.quote(invocation.replace("setsid codex", "codex")))
            script = f"setsid {launch} & p=$!; echo $p > {shlex.quote(home + '/agent.pid')}; wait $p"
            result = await self.exec_as_agent(environment, command=script, env=env)
            result_code = result.return_code
            if result_code:
                raise RuntimeError("Codex exited unsuccessfully; inspect redacted trial logs")
        finally:
            # Timeout cancellation must stop the container process before copying logs.
            cleanup = (
                "set -e; "
                f"if [ -f {shlex.quote(home + '/agent.pid')} ]; then "
                f"p=$(cat {shlex.quote(home + '/agent.pid')}); "
                "case $p in ''|*[!0-9]*) exit 2;; esac; "
                "kill -TERM -- -$p 2>/dev/null || true; sleep 1; kill -KILL -- -$p 2>/dev/null || true; fi; "
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
