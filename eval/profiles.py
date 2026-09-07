from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from eval.paths import PROFILES, profile_path


@dataclass(frozen=True)
class Profile:
    name: str
    role: str
    parent_model: str
    parent_effort: str
    agents_enabled: bool
    subagent_name: str
    subagent_model: str
    subagent_effort: str
    notes: str = ""

    def substitutions(self) -> dict[str, str]:
        return {
            "PARENT_MODEL": self.parent_model,
            "PARENT_EFFORT": self.parent_effort,
            "SUBAGENT_NAME": self.subagent_name,
            "SUBAGENT_MODEL": self.subagent_model,
            "SUBAGENT_EFFORT": self.subagent_effort,
            "AGENTS_ENABLED": "true" if self.agents_enabled else "false",
        }


def load_profile(name: str) -> Profile:
    path = profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"unknown profile: {name} ({path})")
    raw = yaml.safe_load(path.read_text())
    return Profile(
        name=raw["name"],
        role=raw.get("role", "routed"),
        parent_model=raw["parent_model"],
        parent_effort=raw["parent_effort"],
        agents_enabled=bool(raw.get("agents_enabled", True)),
        subagent_name=raw["subagent_name"],
        subagent_model=raw["subagent_model"],
        subagent_effort=raw["subagent_effort"],
        notes=raw.get("notes", ""),
    )


def list_profiles() -> list[str]:
    return sorted(p.stem for p in Path(PROFILES).glob("*.yaml"))
