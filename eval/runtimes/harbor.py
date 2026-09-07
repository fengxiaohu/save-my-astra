from __future__ import annotations

import shutil
from pathlib import Path

from eval.profiles import Profile
from eval.render import write_codex_home
from eval.suites import Item


def harbor_command(
    profile: Profile,
    items: list[Item],
    *,
    out: Path,
    env: str | None = None,
) -> list[str]:
    home = write_codex_home(out / "codex-home", profile, eval_agents=True)
    config = home / "config.toml"
    cmd = [
        "harbor",
        "run",
        "-d",
        "terminal-bench/terminal-bench@4.0.0",
        "--agent",
        "codex",
        "--model",
        f"openai/{profile.parent_model}",
        "--ak",
        f"config={config}",
    ]
    if env:
        cmd.extend(["--env", env])
    for item in items:
        cmd.extend(["--task-name", item.item_id])
    return cmd


def run_harbor(
    profile: Profile,
    items: list[Item],
    *,
    out: Path,
    env: str | None = None,
    print_only: bool = False,
) -> list[str]:
    cmd = harbor_command(profile, items, out=out, env=env)
    if print_only:
        return cmd
    if shutil.which("harbor") is None:
        raise RuntimeError("harbor not on PATH. uv tool install 'harbor[modal]'")
    import subprocess

    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"harbor exited {proc.returncode}")
    return cmd
