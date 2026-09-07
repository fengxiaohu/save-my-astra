from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from eval.paths import suite_dir
from eval.routing import DelegationExpected, normalize_expected

KNOWN = (
    "terminal-bench-4",
    "swebench-pro",
    "math-500",
    "aime-2025",
    "frontiermath-public12",
)


@dataclass
class Item:
    item_id: str
    prompt: str
    answer: str | None = None
    delegation_expected: DelegationExpected = "optional"
    extra: dict = field(default_factory=dict)


@dataclass
class Suite:
    name: str
    role: str
    question: str
    n_default: int
    default_delegation_expected: DelegationExpected
    raw: dict
    path: Path

    def smoke_ids(self) -> list[str]:
        path = self.path / "smoke_ids.txt"
        if not path.exists():
            return []
        ids = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
        return ids

    def expected_for(self, item_id: str) -> DelegationExpected:
        forced = set(self.raw.get("delegation_expected_true") or [])
        if item_id in forced:
            return "true"
        return self.default_delegation_expected


def load_suite(name: str) -> Suite:
    if name not in KNOWN:
        raise FileNotFoundError(f"unknown suite: {name}. known={list(KNOWN)}")
    path = suite_dir(name)
    raw = yaml.safe_load((path / "suite.yaml").read_text())
    return Suite(
        name=raw["name"],
        role=raw["role"],
        question=raw["question"],
        n_default=int(raw.get("n_default", 1)),
        default_delegation_expected=normalize_expected(
            raw.get("default_delegation_expected", "optional")
        ),
        raw=raw,
        path=path,
    )


def read_id_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
