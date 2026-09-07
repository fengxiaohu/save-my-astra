---
name: codex-astra-routing
description: >-
  Install Codex parent/child routing: GPT-6 Astra as the root agent,
  gpt-5.6-luna (or terra) as a pinned subagent. Use when the user wants
  cheaper Codex subagents, default_subagent_model, or to stop children
  inheriting gpt-6-astra.
---

# Codex Astra routing

Pin GPT-6 Astra as the parent. Pin a cheaper child so spawned agents do not clone Astra.

Do not call this the lowest-cost Codex setup. Subagents often use more tokens; they cost less only when cheap tokens replace expensive ones.

## Install

From this repo, merge into `~/.codex` without touching `model_provider`, MCP servers, `notify`, or plugins:

```bash
./scripts/install.sh                 # default: astra-luna
./scripts/install.sh --profile astra-terra
./scripts/install.sh --dry-run
python -m eval verify --profile astra-luna
```

If the repo is not checked out, apply the same three writes:

1. `~/.codex/config.toml`
   - `model = "gpt-6-astra"`
   - `model_reasoning_effort = "low"`
   - `[agents] enabled = true`
   - `default_subagent_model = "gpt-5.6-luna"`
   - `default_subagent_reasoning_effort = "max"`
   - `[features.multi_agent_v2] hide_spawn_agent_metadata = false`
2. `~/.codex/agents/luna-max-worker.toml` with `model = "gpt-5.6-luna"` (do not omit `model`)
3. `~/.codex/AGENTS.md`: delegate when it can save time or quality; `fork_turns: none`; do not spawn another Astra

Backup the existing `config.toml` and `AGENTS.md` first. Merge; do not replace the whole file.

Luna spawn allowlists sometimes reject the child. Retry once with `astra-terra` (`gpt-5.6-terra` / `max`) and say so.

## Verify

Start a **new** Codex session. Ask it to spawn one read-only repo scan. The child model must be `gpt-5.6-luna` or `gpt-5.6-terra`, never `gpt-6-astra`.

ChatGPT-login sessions may ignore per-spawn `model`. Defaults in `config.toml` plus the worker file are the reliable path.
