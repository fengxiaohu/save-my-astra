import hashlib
import tomllib
from pathlib import Path

import pytest

from eval.adapters.terminal_bench import load_terminal_bench
from eval.phase3_config import (
    ARMS,
    TASK_IDS,
    freeze_inputs,
    phase3_profile,
    schedule,
    write_phase3_home,
)


def _make_tasks(root: Path, *, timeout: int = 120) -> None:
    for task_id in TASK_IDS:
        task = root / task_id
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        (task / "instruction.md").write_text(f"# {task_id}\n")
        (task / "environment" / "Dockerfile").write_text("FROM scratch\n")
        (task / "tests" / "test.sh").write_text("#!/bin/sh\n")
        (task / "task.toml").write_text(
            f"[agent]\ntimeout_sec = {timeout}\n\n"
            "[verifier]\ntimeout_sec = 30\n\n"
            "[environment]\nbuild_timeout_sec = 45\n"
        )


def test_phase3_schedule_is_fixed_and_balanced():
    assert ARMS == ("astra-solo", "astra-luna")
    assert schedule() == [
        {"task": TASK_IDS[0], "arm": "astra-solo"},
        {"task": TASK_IDS[0], "arm": "astra-luna"},
        {"task": TASK_IDS[1], "arm": "astra-luna"},
        {"task": TASK_IDS[1], "arm": "astra-solo"},
        {"task": TASK_IDS[2], "arm": "astra-solo"},
        {"task": TASK_IDS[2], "arm": "astra-luna"},
        {"task": TASK_IDS[3], "arm": "astra-luna"},
        {"task": TASK_IDS[3], "arm": "astra-solo"},
    ]


def test_phase3_profiles_keep_daily_profiles_unchanged():
    solo = phase3_profile("solo")
    routed = phase3_profile("routed")
    assert solo.parent_effort == routed.parent_effort == "medium"
    assert solo.agents_enabled is False
    assert routed.agents_enabled is True
    assert routed.subagent_model == "gpt-5.6-luna"
    assert routed.subagent_effort == "max"
    assert phase3_profile("astra-solo").parent_effort == "medium"


def test_phase3_home_has_only_luna_worker(tmp_path: Path):
    solo_home = write_phase3_home(tmp_path / "solo", "solo")
    solo_config = tomllib.loads((solo_home / "config.toml").read_text())
    assert solo_config["model"] == "gpt-6-astra"
    assert solo_config["model_reasoning_effort"] == "medium"
    assert solo_config["agents"]["enabled"] is False
    assert not (solo_home / "agents").exists()
    assert "gpt-6-astra" in (solo_home / "AGENTS.md").read_text()

    routed_home = write_phase3_home(tmp_path / "routed", "routed")
    routed_config = tomllib.loads((routed_home / "config.toml").read_text())
    assert routed_config["agents"]["default_subagent_model"] == "gpt-5.6-luna"
    workers = list((routed_home / "agents").glob("*.toml"))
    assert [worker.name for worker in workers] == ["luna-max-worker.toml"]
    assert "terra" not in (routed_home / "AGENTS.md").read_text().lower()
    assert "spawn" in workers[0].read_text().lower()


def test_phase3_slice_is_explicit_and_limit_cannot_shorten():
    items = load_terminal_bench("phase3a")
    assert tuple(item.item_id for item in items) == TASK_IDS
    with pytest.raises(ValueError, match="cannot shorten"):
        load_terminal_bench("phase3a", limit=1)


def test_freeze_inputs_hashes_tasks_sources_and_preserves_shorter_timeout(tmp_path: Path):
    _make_tasks(tmp_path, timeout=120)
    manifest = freeze_inputs(tmp_path, "0.1.0-test")
    assert manifest["task_ids"] == list(TASK_IDS)
    assert set(manifest["tasks"]) == set(TASK_IDS)
    first = manifest["tasks"][TASK_IDS[0]]
    assert first["agent_timeout_seconds_raw"] == 120
    assert first["agent_timeout_seconds"] == 120
    assert first["verifier_timeout_seconds"] == 30
    assert first["setup_timeout_seconds"] == 45
    assert first["sha256"]
    assert first["sha256_tree"]
    assert manifest["source_sha256"]["eval/phase3_config.py"] == hashlib.sha256(
        Path("eval/phase3_config.py").read_bytes()
    ).hexdigest()
    assert manifest["versions"]["codex"] == "0.1.0-test"
    assert manifest["dependency_versions"]


def test_longer_task_timeout_is_capped(tmp_path):
    _make_tasks(tmp_path, timeout=9000)
    assert freeze_inputs(tmp_path, "0.1.0")["tasks"][TASK_IDS[0]]["agent_timeout_seconds"] == 3600


@pytest.mark.parametrize("version", ["latest", "^0.1.0", ">=0.1.0", "", "0.1"])
def test_unpinned_codex_versions_are_rejected(tmp_path, version):
    with pytest.raises(ValueError):
        freeze_inputs(tmp_path, version)


def test_existing_home_is_not_reused_for_baseline(tmp_path):
    write_phase3_home(tmp_path / "home", "astra-luna")
    with pytest.raises(ValueError):
        write_phase3_home(tmp_path / "home", "astra-solo")


def test_invalid_timeout_is_rejected(tmp_path):
    _make_tasks(tmp_path, timeout=-1)
    with pytest.raises(ValueError, match="positive"):
        freeze_inputs(tmp_path, "0.1.0")


def test_freeze_is_json_roundtrip_stable(tmp_path, monkeypatch):
    import json
    from eval import phase3_config
    _make_tasks(tmp_path)
    monkeypatch.setattr(phase3_config, "_version_evidence", lambda command: {"version": "fixture"})
    frozen = freeze_inputs(tmp_path, "0.1.0")
    assert json.loads(json.dumps(frozen)) == frozen


def test_freeze_inputs_rejects_extra_task_and_escaping_symlink(tmp_path: Path):
    _make_tasks(tmp_path)
    (tmp_path / "unexpected").mkdir()
    with pytest.raises(ValueError, match="exactly"):
        freeze_inputs(tmp_path, "0.1.0")

    (tmp_path / "unexpected").rmdir()
    outside = tmp_path.parent / "phase3-outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret").write_text("secret")
    link = tmp_path / TASK_IDS[0] / "escape"
    link.symlink_to(outside / "secret")
    with pytest.raises(ValueError, match="escapes"):
        freeze_inputs(tmp_path, "0.1.0")
