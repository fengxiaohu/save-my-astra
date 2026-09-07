from __future__ import annotations

import pytest

from eval.cli import main
from eval.run import run_eval


def test_cli_rejects_nonpositive_n():
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "run",
                "--suite",
                "math-500",
                "--n",
                "0",
                "--dry-run",
            ]
        )
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "run",
                "--suite",
                "math-500",
                "--n",
                "-1",
                "--dry-run",
            ]
        )
    assert exc.value.code == 2


def test_cli_rejects_nonpositive_limit():
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "run",
                "--suite",
                "math-500",
                "--limit",
                "0",
                "--dry-run",
            ]
        )
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "run",
                "--suite",
                "math-500",
                "--limit",
                "-1",
                "--dry-run",
            ]
        )
    assert exc.value.code == 2


def test_run_eval_rejects_nonpositive_n():
    with pytest.raises(ValueError, match="n must be >= 1"):
        run_eval(
            profile="astra-luna",
            baseline="astra-solo",
            suite_name="math-500",
            slice_name="smoke",
            runtime="api",
            n=0,
            limit=None,
            dry_run=True,
            print_cmd=False,
        )


def test_run_eval_rejects_nonpositive_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        run_eval(
            profile="astra-luna",
            baseline="astra-solo",
            suite_name="math-500",
            slice_name="smoke",
            runtime="api",
            n=None,
            limit=-1,
            dry_run=True,
            print_cmd=False,
        )
