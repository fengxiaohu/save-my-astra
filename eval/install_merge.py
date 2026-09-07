from __future__ import annotations

import re
from pathlib import Path

from eval.paths import SKILL_FILE, SKILL_NAME
from eval.profiles import load_profile
from eval.render import render_template

MODEL_KEYS = ("model =", "model_reasoning_effort =")

AGENT_SCALAR_KEYS = frozenset(
    {
        "enabled",
        "default_subagent_model",
        "default_subagent_reasoning_effort",
        "max_concurrent_threads_per_session",
        "max_threads",
        "interrupt_message",
        "max_depth",
        "job_max_runtime_seconds",
    }
)

_ASSIGN = re.compile(r"^([A-Za-z0-9_.-]+)\s*=")
_TABLE = re.compile(r"^\[", re.M)


def _replace_or_insert_top(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}\s*.*$", re.M)
    line = f"{key} {value}"
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    return line + "\n" + text


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Span of `[heading]` through the line before the next table header.

    Table headers are lines that start with `[`. Inline arrays such as
    `notify = ["/bin/true", "turn-ended"]` must not end the section.
    """
    header = re.search(rf"^\[{re.escape(heading)}\][ \t]*$", text, re.M)
    if not header:
        return None
    rest_start = header.end()
    nxt = _TABLE.search(text[rest_start:])
    end = rest_start + nxt.start() if nxt else len(text)
    return header.start(), end


def _root_end(text: str) -> int:
    match = _TABLE.search(text)
    return match.start() if match else len(text)


def _root_has_key(text: str, key: str) -> bool:
    return re.search(rf"^{re.escape(key)}\s*=", text[: _root_end(text)], re.M) is not None


def _insert_before_first_table(text: str, block: str) -> str:
    chunk = block if block.endswith("\n") else block + "\n"
    if not text.strip():
        return chunk
    idx = _root_end(text)
    prefix = text[:idx].rstrip() + "\n\n"
    suffix = text[idx:].lstrip("\n")
    if not suffix:
        return prefix + chunk
    return prefix + chunk + ("\n" if chunk.endswith("\n\n") else "\n") + suffix


def _split_agents_body(body: str) -> tuple[str, str]:
    stray: list[str] = []
    agent: list[str] = []
    collecting_stray = False
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            (stray if collecting_stray else agent).append(line)
            continue
        match = _ASSIGN.match(stripped)
        if match:
            collecting_stray = match.group(1) not in AGENT_SCALAR_KEYS
            (stray if collecting_stray else agent).append(line)
        else:
            (stray if collecting_stray else agent).append(line)
    return "".join(agent), "".join(stray)


def _stray_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _ASSIGN.match(stripped)
    return match.group(1) if match else None


def _filter_stray_already_at_root(text: str, stray: str) -> str:
    kept: list[str] = []
    skip_key: str | None = None
    for line in stray.splitlines(keepends=True):
        key = _stray_key(line)
        if key is not None:
            skip_key = key if _root_has_key(text, key) else None
        if skip_key is None:
            kept.append(line)
    return "".join(kept)


def _hoist_stray_from_agents(text: str) -> str:
    span = _section_span(text, "agents")
    if not span:
        return text
    start, end = span
    raw = text[start:end]
    newline = raw.find("\n")
    body = raw[newline + 1 :] if newline != -1 else ""
    agent_body, stray = _split_agents_body(body)
    stray = _filter_stray_already_at_root(text, stray)
    if not stray.strip():
        return text
    heading = raw[: newline + 1] if newline != -1 else raw + "\n"
    without_stray = text[:start] + heading + agent_body + text[end:]
    return _insert_before_first_table(without_stray, stray.rstrip() + "\n")


def _replace_section(text: str, heading: str, body: str, *, after_root: bool = False) -> str:
    block = f"[{heading}]\n{body.rstrip()}\n\n"
    span = _section_span(text, heading)
    if span:
        start, end = span
        return text[:start] + block + text[end:].lstrip("\n")
    if after_root:
        return _insert_before_first_table(text, block)
    return text.rstrip() + "\n\n" + block


def merge_snippet(existing: str, profile_name: str) -> str:
    profile = load_profile(profile_name)
    text = existing if existing.strip() else ""
    text = _replace_or_insert_top(text, "model =", f'"{profile.parent_model}"')
    text = _replace_or_insert_top(
        text, "model_reasoning_effort =", f'"{profile.parent_effort}"'
    )
    text = _hoist_stray_from_agents(text)
    agents_body = (
        f"enabled = {'true' if profile.agents_enabled else 'false'}\n"
        f'default_subagent_model = "{profile.subagent_model}"\n'
        f'default_subagent_reasoning_effort = "{profile.subagent_effort}"\n'
        "max_concurrent_threads_per_session = 4\n"
    )
    text = _replace_section(text, "agents", agents_body, after_root=True)
    text = _replace_section(text, "features.multi_agent_v2", "hide_spawn_agent_metadata = false\n")
    return text if text.endswith("\n") else text + "\n"


def worker_filename(profile_name: str) -> str:
    profile = load_profile(profile_name)
    return f"{profile.subagent_name.replace('_', '-')}.toml"


def write_install(codex_home: Path, profile_name: str) -> None:
    profile = load_profile(profile_name)
    (codex_home / "AGENTS.md").write_text(render_template("AGENTS.md", profile))
    agents_dir = codex_home / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / worker_filename(profile_name)).write_text(
        render_template("agents/worker.toml", profile)
    )
    config = codex_home / "config.toml"
    existing = config.read_text() if config.exists() else ""
    config.write_text(merge_snippet(existing, profile_name))
    skill_src = SKILL_FILE
    if skill_src.exists():
        skill_dst = codex_home / "skills" / SKILL_NAME / "SKILL.md"
        skill_dst.parent.mkdir(parents=True, exist_ok=True)
        skill_dst.write_text(skill_src.read_text())


def verify_install(codex_home: Path, profile_name: str) -> tuple[list[str], dict[str, str]]:
    """Check a CODEX_HOME after install. Empty error list means OK."""
    import tomllib

    profile = load_profile(profile_name)
    errors: list[str] = []
    config_path = codex_home / "config.toml"
    worker_path = codex_home / "agents" / worker_filename(profile_name)
    summary = {
        "profile": profile.name,
        "parent": f"{profile.parent_model} / {profile.parent_effort}",
        "child": f"{profile.subagent_model} / {profile.subagent_effort}",
        "worker": worker_path.name,
        "agents_enabled": str(profile.agents_enabled).lower(),
        "hide_spawn_agent_metadata": "false",
    }
    if not config_path.exists():
        return [f"missing {config_path}"], summary
    data = tomllib.loads(config_path.read_text())
    if data.get("model") != profile.parent_model:
        errors.append(f"config model is {data.get('model')!r}, want {profile.parent_model!r}")
    if data.get("model_reasoning_effort") != profile.parent_effort:
        errors.append(
            "config model_reasoning_effort is "
            f"{data.get('model_reasoning_effort')!r}, want {profile.parent_effort!r}"
        )
    agents = data.get("agents") or {}
    if bool(agents.get("enabled")) != profile.agents_enabled:
        errors.append(f"agents.enabled is {agents.get('enabled')!r}, want {profile.agents_enabled}")
    if agents.get("default_subagent_model") != profile.subagent_model:
        errors.append(
            "default_subagent_model is "
            f"{agents.get('default_subagent_model')!r}, want {profile.subagent_model!r}"
        )
    if agents.get("default_subagent_reasoning_effort") != profile.subagent_effort:
        errors.append(
            "default_subagent_reasoning_effort is "
            f"{agents.get('default_subagent_reasoning_effort')!r}, want {profile.subagent_effort!r}"
        )
    hidden = (data.get("features") or {}).get("multi_agent_v2") or {}
    if hidden.get("hide_spawn_agent_metadata") is not False:
        errors.append("features.multi_agent_v2.hide_spawn_agent_metadata must be false")
        summary["hide_spawn_agent_metadata"] = str(
            hidden.get("hide_spawn_agent_metadata")
        ).lower()
    if profile.agents_enabled and profile.subagent_model == profile.parent_model:
        errors.append("routed profile child model clones the parent")
    if not worker_path.exists():
        errors.append(f"missing worker {worker_path}")
        return errors, summary
    worker = tomllib.loads(worker_path.read_text())
    if worker.get("model") != profile.subagent_model:
        errors.append(
            f"worker model is {worker.get('model')!r}, want {profile.subagent_model!r}"
        )
    if worker.get("model_reasoning_effort") != profile.subagent_effort:
        errors.append(
            "worker model_reasoning_effort is "
            f"{worker.get('model_reasoning_effort')!r}, want {profile.subagent_effort!r}"
        )
    agents_md = (codex_home / "AGENTS.md").read_text() if (codex_home / "AGENTS.md").exists() else ""
    if profile.agents_enabled:
        if profile.subagent_model not in agents_md:
            errors.append("AGENTS.md does not name the child model")
        if "fork_turns: none" not in agents_md:
            errors.append("AGENTS.md missing fork_turns: none")
    return errors, summary


def format_verify(errors: list[str], summary: dict[str, str]) -> str:
    lines = [
        f"profile: {summary['profile']}",
        f"parent:  {summary['parent']}",
        f"child:   {summary['child']}",
        f"worker:  {summary['worker']}",
        f"agents.enabled: {summary['agents_enabled']}",
        f"hide_spawn_agent_metadata: {summary['hide_spawn_agent_metadata']}",
    ]
    if errors:
        lines.append("verify: FAIL")
        lines.extend(f"- {err}" for err in errors)
    else:
        lines.append("verify: OK")
    return "\n".join(lines)
