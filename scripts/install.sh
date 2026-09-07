#!/usr/bin/env bash
# Merge routing templates into ~/.codex without touching model_provider / MCP / notify / plugins.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="astra-luna"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
DRY_RUN=0

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

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ "$DRY_RUN" == "1" ]]; then
  python3 - <<PY
from pathlib import Path
from eval.install_merge import merge_snippet
from eval.profiles import load_profile
from eval.render import render_template
p = load_profile("$PROFILE")
print("profile:", p.name)
print("--- AGENTS.md ---")
print(render_template("AGENTS.md", p))
print("--- config merge preview ---")
existing = Path("$CODEX_HOME/config.toml").read_text() if Path("$CODEX_HOME/config.toml").exists() else ""
print(merge_snippet(existing, "$PROFILE"))
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

python3 - <<PY
from pathlib import Path
from eval.install_merge import format_verify, verify_install, write_install

home = Path("$CODEX_HOME")
write_install(home, "$PROFILE")
errors, summary = verify_install(home, "$PROFILE")
print("installed $PROFILE into $CODEX_HOME")
print("backup: $backup")
print(format_verify(errors, summary))
if errors:
    raise SystemExit(1)
PY
