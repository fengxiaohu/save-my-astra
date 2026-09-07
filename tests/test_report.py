from __future__ import annotations

import json
from pathlib import Path

import pytest

import eval.report as report_mod
from eval.report import write_report


def _payload(*, run_id: str = "20260101T000000Z-deadbeef0001") -> dict:
    return {
        "suite": "math-500",
        "profile": "astra-luna",
        "baseline": "astra-solo",
        "role": "cost_calibration",
        "question": "test question",
        "runtime": "api",
        "n": 1,
        "run_id": run_id,
        "arms": [],
    }


def test_write_report_uses_run_id_and_exclusive_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(report_mod, "REPORTS", tmp_path)
    payload = _payload()
    path = write_report(payload)
    assert path.name == "20260101T000000Z-deadbeef0001-math-500-astra-luna.json"
    assert json.loads(path.read_text())["run_id"] == payload["run_id"]
    assert path.with_suffix(".md").exists()
    assert "Run: `20260101T000000Z-deadbeef0001`" in path.with_suffix(".md").read_text()

    with pytest.raises(FileExistsError):
        write_report(payload)


def test_write_report_generates_run_id_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(report_mod, "REPORTS", tmp_path)
    payload = _payload()
    del payload["run_id"]
    path = write_report(payload)
    assert path.exists()
    saved = json.loads(path.read_text())
    assert saved.get("run_id") is None
    assert path.stem.startswith("")


def test_run_eval_includes_unique_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(report_mod, "REPORTS", tmp_path)
    from eval.run import run_eval

    path1 = run_eval(
        profile="astra-luna",
        baseline="astra-solo",
        suite_name="math-500",
        slice_name="smoke",
        runtime="api",
        n=1,
        limit=1,
        dry_run=True,
        print_cmd=False,
    )
    path2 = run_eval(
        profile="astra-luna",
        baseline="astra-solo",
        suite_name="math-500",
        slice_name="smoke",
        runtime="api",
        n=1,
        limit=1,
        dry_run=True,
        print_cmd=False,
    )
    run_id1 = json.loads(path1.read_text())["run_id"]
    run_id2 = json.loads(path2.read_text())["run_id"]
    assert run_id1 != run_id2
