from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"
TEMPLATES = ROOT / "templates"
TASKS = ROOT / "tasks"
REPORTS = ROOT / "reports"
DATA = ROOT / "data"
EVAL_CODEX = ROOT / ".eval-codex"
PRICING = Path(__file__).resolve().parent / "pricing.yaml"
SKILL_NAME = "save-my-astra"
SKILL_FILE = ROOT / "skills" / SKILL_NAME / "SKILL.md"


def profile_path(name: str) -> Path:
    return PROFILES / f"{name}.yaml"


def suite_dir(name: str) -> Path:
    return TASKS / name
