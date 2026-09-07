from __future__ import annotations

from pathlib import Path

from eval.paths import TEMPLATES
from eval.profiles import Profile, load_profile


def render_text(template: str, profile: Profile) -> str:
    text = template
    for key, value in profile.substitutions().items():
        text = text.replace("{{" + key + "}}", value)
    return text


def render_template(name: str, profile: Profile) -> str:
    path = TEMPLATES / name
    return render_text(path.read_text(), profile)


def write_codex_home(out: Path, profile: Profile, *, eval_agents: bool) -> Path:
    """Write a self-contained CODEX_HOME fragment (no MCP, no provider secrets)."""
    out.mkdir(parents=True, exist_ok=True)
    agents_name = "AGENTS.eval.md" if eval_agents else "AGENTS.md"
    (out / "AGENTS.md").write_text(render_template(agents_name, profile))
    snippet = render_template("config.snippet.toml", profile)
    (out / "config.toml").write_text(snippet + "\n")
    worker_dir = out / "agents"
    worker_dir.mkdir(exist_ok=True)
    worker_name = f"{profile.subagent_name.replace('_', '-')}.toml"
    (worker_dir / worker_name).write_text(render_template("agents/worker.toml", profile))
    return out


def render_profile_to(name: str, out: Path, *, eval_agents: bool = True) -> Path:
    return write_codex_home(out, load_profile(name), eval_agents=eval_agents)
