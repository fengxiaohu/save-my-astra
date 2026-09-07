import tomllib
from pathlib import Path

from eval.check import check
from eval.install_merge import merge_snippet
from eval.profiles import load_profile
from eval.render import render_template, write_codex_home


def test_check_clean():
    assert check() == []


def test_eval_agents_allow_no_spawn(tmp_path: Path):
    profile = load_profile("astra-luna")
    text = render_template("AGENTS.eval.md", profile)
    assert "luna_max_worker" in text
    assert "gpt-5.6-luna" in text
    assert "Do not spawn a child in order to spawn a child" in text


def test_write_codex_home(tmp_path: Path):
    dest = write_codex_home(tmp_path, load_profile("astra-luna"), eval_agents=True)
    assert (dest / "AGENTS.md").exists()
    assert (dest / "config.toml").exists()
    assert list((dest / "agents").glob("*.toml"))


def test_merge_keeps_provider():
    existing = 'model_provider = "cc-switch-official"\nmodel = "old"\n\n[mcp_servers.x]\nurl = "https://example.com"\n'
    merged = merge_snippet(existing, "astra-luna")
    data = tomllib.loads(merged)
    assert data["model_provider"] == "cc-switch-official"
    assert data["model"] == "gpt-6-astra"
    assert data["mcp_servers"]["x"]["url"] == "https://example.com"
    assert "agents" in data


def test_merge_keeps_root_notify():
    existing = (
        'model_provider = "cc-switch-official"\n'
        'model = "old"\n'
        'notify = ["/bin/true", "turn-ended"]\n\n'
        "[mcp_servers.x]\n"
        'url = "https://example.com"\n'
    )
    merged = merge_snippet(existing, "astra-luna")
    data = tomllib.loads(merged)
    assert data["notify"] == ["/bin/true", "turn-ended"]
    assert "notify" not in data.get("agents", {})
    assert data["model_provider"] == "cc-switch-official"
    assert data["mcp_servers"]["x"]["url"] == "https://example.com"


def test_write_install_verifies(tmp_path: Path):
    from eval.install_merge import verify_install, write_install

    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "keep-me"\nmodel = "old"\n\n[mcp_servers.x]\nurl = "https://example.com"\n'
    )
    write_install(home, "astra-luna")
    errors, summary = verify_install(home, "astra-luna")
    assert errors == []
    assert summary["parent"] == "gpt-6-astra / low"
    assert summary["child"] == "gpt-5.6-luna / max"
    data = tomllib.loads((home / "config.toml").read_text())
    assert data["model_provider"] == "keep-me"
    assert data["mcp_servers"]["x"]["url"] == "https://example.com"
    worker = (home / "agents" / "luna-max-worker.toml").read_text()
    assert 'model = "gpt-5.6-luna"' in worker
    skill = home / "skills" / "save-my-astra" / "SKILL.md"
    assert skill.exists()
    assert "default_subagent_model" in skill.read_text()


def test_verify_detects_cloned_child(tmp_path: Path):
    from eval.install_merge import verify_install, write_install

    home = tmp_path / "codex"
    home.mkdir()
    write_install(home, "astra-luna")
    worker = home / "agents" / "luna-max-worker.toml"
    worker.write_text(worker.read_text().replace("gpt-5.6-luna", "gpt-6-astra"))
    errors, _ = verify_install(home, "astra-luna")
    assert errors
    assert any("worker model" in err for err in errors)


def test_merge_recovers_swallowed_notify():
    existing = (
        'model = "old"\n\n'
        "[agents]\n"
        "enabled = false\n"
        'notify = ["/bin/true", "turn-ended"]\n\n'
        "[mcp_servers.x]\n"
        'url = "https://example.com"\n'
    )
    merged = merge_snippet(existing, "astra-luna")
    data = tomllib.loads(merged)
    assert data["notify"] == ["/bin/true", "turn-ended"]
    assert "notify" not in data.get("agents", {})
    assert data["agents"]["enabled"] is True
    assert data["mcp_servers"]["x"]["url"] == "https://example.com"
