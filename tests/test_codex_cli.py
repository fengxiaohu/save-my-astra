from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.profiles import load_profile
from eval.runtimes import codex_cli
from eval.suites import Item


def _completed_turn() -> str:
    return (
        '{"type":"turn.completed","usage":'
        '{"input_tokens":100,"output_tokens":10,"total_tokens":110}}'
    )


def _spawned_turn() -> str:
    return (
        '{"type":"item.completed","item":{"tool":"spawn_agent"}}\n'
        + _completed_turn()
    )


def test_plus_auth_is_available_only_while_codex_runs(tmp_path: Path, monkeypatch):
    login_home = tmp_path / "login-home"
    login_home.mkdir()
    auth = login_home / "auth.json"
    auth.write_text("secret")
    eval_home = tmp_path / "eval-home"
    eval_home.mkdir()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", "login-home")
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_cli, "write_codex_home", lambda *args, **kwargs: eval_home)

    def run(*args, **kwargs):
        link = eval_home / "auth.json"
        assert link.is_symlink()
        assert link.resolve() == auth
        return SimpleNamespace(returncode=0, stdout=_completed_turn(), stderr="")

    monkeypatch.setattr(codex_cli.subprocess, "run", run)

    _, usage = codex_cli.run_codex(
        Item(item_id="one", prompt="solve"),
        load_profile("astra-solo"),
        run_id="test",
    )

    assert not (eval_home / "auth.json").exists()
    assert list(usage.by_model) == ["gpt-6-astra"]
    assert usage.total_tokens == 110


def test_plus_auth_link_is_removed_when_codex_fails(tmp_path: Path, monkeypatch):
    login_home = tmp_path / "login-home"
    login_home.mkdir()
    (login_home / "auth.json").write_text("secret")
    eval_home = tmp_path / "eval-home"
    eval_home.mkdir()

    monkeypatch.setenv("CODEX_HOME", str(login_home))
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_cli, "write_codex_home", lambda *args, **kwargs: eval_home)

    def run(*args, **kwargs):
        auth_link = eval_home / "auth.json"
        auth_link.unlink()
        auth_link.write_text("refreshed secret")
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr(codex_cli.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="codex exec failed"):
        codex_cli.run_codex(
            Item(item_id="one", prompt="solve"),
            load_profile("astra-solo"),
            run_id="test",
        )

    assert not (eval_home / "auth.json").exists()


def test_stale_eval_auth_is_rejected_without_login_auth(tmp_path: Path, monkeypatch):
    login_home = tmp_path / "login-home"
    login_home.mkdir()
    eval_home = tmp_path / "eval-home"
    eval_home.mkdir()
    (eval_home / "auth.json").write_text("stale secret")

    monkeypatch.setenv("CODEX_HOME", str(login_home))
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_cli, "write_codex_home", lambda *args, **kwargs: eval_home)
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("codex must not run with stale credentials"),
    )

    with pytest.raises(RuntimeError, match="already contains"):
        codex_cli.run_codex(
            Item(item_id="one", prompt="solve"),
            load_profile("astra-solo"),
            run_id="test",
        )


def test_spawned_unknown_usage_is_not_attributed_to_parent(tmp_path: Path, monkeypatch):
    login_home = tmp_path / "login-home"
    login_home.mkdir()
    eval_home = tmp_path / "eval-home"
    eval_home.mkdir()

    monkeypatch.setenv("CODEX_HOME", str(login_home))
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(codex_cli, "write_codex_home", lambda *args, **kwargs: eval_home)
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=_spawned_turn(), stderr=""
        ),
    )

    _, usage = codex_cli.run_codex(
        Item(item_id="one", prompt="solve"),
        load_profile("astra-luna"),
        run_id="test",
    )

    assert usage.spawn_count > 0
    assert list(usage.by_model) == ["unknown"]
