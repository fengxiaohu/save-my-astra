from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from eval.paths import EVAL_CODEX
from eval.profiles import Profile
from eval.render import write_codex_home
from eval.runtimes.spawn_scan import count_spawns
from eval.suites import Item
from eval.usage import RunUsage


def _parse_json_lines(stdout: str) -> tuple[str, RunUsage]:
    text_parts: list[str] = []
    usage = RunUsage()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type") or event.get("event") or ""
        if kind in {"item.completed", "agent.message", "message"}:
            payload = event.get("item") or event
            content = payload.get("text") or payload.get("content") or ""
            if isinstance(content, str) and content:
                text_parts.append(content)
        if kind in {"turn.completed", "turn_completed"}:
            raw = (event.get("usage") or event.get("turn", {}).get("usage") or {})
            model = raw.get("model") or event.get("model") or "unknown"
            usage.add(
                model,
                input_tokens=int(raw.get("input_tokens") or raw.get("input") or 0),
                output_tokens=int(raw.get("output_tokens") or raw.get("output") or 0),
                total_tokens=int(raw.get("total_tokens") or raw.get("total") or 0),
            )
        usage.spawn_count += count_spawns(event)
    text = "\n".join(text_parts) or stdout
    if usage.spawn_count == 0:
        usage.spawn_count = count_spawns(stdout)
    return text, usage


def run_codex(item: Item, profile: Profile, *, run_id: str) -> tuple[str, RunUsage]:
    if shutil.which("codex") is None:
        raise RuntimeError("codex CLI not on PATH")
    home = write_codex_home(EVAL_CODEX / run_id / profile.name, profile, eval_agents=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    proc = subprocess.run(
        ["codex", "exec", "--json", item.prompt],
        cwd=home,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    combined = proc.stdout + "\n" + proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"codex exec failed ({proc.returncode}): {combined[-2000:]}")
    return _parse_json_lines(combined)
