#!/usr/bin/env bash
# Merge routing templates into ~/.codex without touching model_provider / MCP / notify / plugins.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="astra-luna"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
DRY_RUN=0

ALLOWED_PROFILES="astra-luna|astra-terra|astra-solo|sol-luna"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --codex-home) CODEX_HOME="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--profile astra-luna|astra-terra|astra-solo|sol-luna] [--codex-home DIR] [--dry-run]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$PROFILE" in
  astra-luna|astra-terra|astra-solo|sol-luna) ;;
  *)
    echo "invalid profile: $PROFILE (allowed: astra-luna, astra-terra, astra-solo, sol-luna)" >&2
    exit 2
    ;;
esac

if [[ -z "$CODEX_HOME" ]]; then
  echo "CODEX_HOME must not be empty" >&2
  exit 2
fi
if [[ "$CODEX_HOME" == *$'\n'* ]]; then
  echo "CODEX_HOME must not contain newlines" >&2
  exit 2
fi

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ "$DRY_RUN" == "1" ]]; then
  export ASTRA_PROFILE="$PROFILE"
  export ASTRA_CODEX_HOME="$CODEX_HOME"
  python3 - <<'PY'
import os
from pathlib import Path

from eval.install_merge import merge_snippet
from eval.profiles import load_profile
from eval.render import render_template

profile = os.environ["ASTRA_PROFILE"]
codex_home = Path(os.environ["ASTRA_CODEX_HOME"])
p = load_profile(profile)
print("profile:", p.name)
print("--- AGENTS.md ---")
print(render_template("AGENTS.md", p))
print("--- config merge preview ---")
config_path = codex_home / "config.toml"
existing = config_path.read_text() if config_path.exists() else ""
print(merge_snippet(existing, profile))
PY
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$CODEX_HOME/backups/save-my-astra-$stamp"
mkdir -p "$backup" "$CODEX_HOME/agents"

if [[ -f "$CODEX_HOME/AGENTS.md" ]]; then
  cp "$CODEX_HOME/AGENTS.md" "$backup/AGENTS.md"
fi
if [[ -f "$CODEX_HOME/config.toml" ]]; then
  cp "$CODEX_HOME/config.toml" "$backup/config.toml"
fi

export ASTRA_PROFILE="$PROFILE"
export ASTRA_CODEX_HOME="$CODEX_HOME"
export ASTRA_BACKUP="$backup"
python3 - <<'PY'
import os
from pathlib import Path

from eval.install_merge import format_verify, verify_install, write_install

profile = os.environ["ASTRA_PROFILE"]
codex_home = Path(os.environ["ASTRA_CODEX_HOME"])
backup = os.environ["ASTRA_BACKUP"]
write_install(codex_home, profile)
errors, summary = verify_install(codex_home, profile)
print(f"installed {profile} into {codex_home}")
print(f"backup: {backup}")
print(format_verify(errors, summary))
if errors:
    raise SystemExit(1)
PY
