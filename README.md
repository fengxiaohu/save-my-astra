# Codex Astra Routing

Codex: Astra parent, Luna/Terra children. Stop subagents cloning the expensive model.

Default profile `astra-luna` sets `gpt-6-astra` (`low`) as the root agent and pins `gpt-5.6-luna` (`max`) as the child. This is a hypothesis to install and measure, not a claim that the pairing is cheapest.

## Install

Does not overwrite `model_provider`, MCP servers, notify paths, or plugins. Existing `config.toml` and `AGENTS.md` are copied to `~/.codex/backups/` first.

```bash
./scripts/install.sh                 # default profile: astra-luna
./scripts/install.sh --profile astra-terra
./scripts/install.sh --dry-run
python -m eval verify --profile astra-luna
```

Or tell Codex: install astra-luna routing from this repo. The skill is `skills/codex-astra-routing/SKILL.md`.

Then start a **new** Codex session. Spawn one read-only repo scan. The child must be `gpt-5.6-luna` (or Terra if you installed that profile), not `gpt-6-astra`.

| Profile | Parent | Child |
| --- | --- | --- |
| `astra-luna` | `gpt-6-astra` / `low` | `gpt-5.6-luna` / `max` |
| `astra-terra` | `gpt-6-astra` / `low` | `gpt-5.6-terra` / `max` |
| `astra-solo` | `gpt-6-astra` / `low` | off (baseline) |
| `sol-luna` | `gpt-5.6-sol` / `low` | `gpt-5.6-luna` / `max` |

Use `astra-terra` when Luna is missing from the spawn allowlist.

## What this writes

- `~/.codex/config.toml` — parent model, `[agents]` defaults, `hide_spawn_agent_metadata = false`
- `~/.codex/agents/luna-max-worker.toml` — child model pinned
- `~/.codex/AGENTS.md` — delegate when it can save time or quality; do not clone the parent
- `~/.codex/skills/codex-astra-routing/SKILL.md` — the same one-liner for later sessions

Plus quota is not API spend. Subagent runs can use more tokens and still cost less if cheap tokens replace expensive ones.

## Measure (optional)

Eval adapters live under `eval/`. Method and claim rules: [docs/method.md](docs/method.md).

```bash
python -m pip install -e ".[dev]"
python -m eval check
```
