# Save My Astra 🚀

**Stop burning GPT-6 Astra tokens on work Luna can do.**

**别让 Luna 能干的活继续烧 GPT-6 Astra 的额度。**

GPT-6 Astra stays as the orchestrator.
Cheap workers handle exploration, scanning, and parallel subagent work.

GPT-6 Astra 继续做编排者；便宜的 worker 负责探索、扫描和并行子 agent 工作。

A tiny Codex routing setup so spawned agents stop cloning GPT-6 Astra.

一套轻量 Codex 路由配置，让 spawn 出来的 agent 不再克隆 GPT-6 Astra。

## The problem / 问题

Without routing:

没有路由时：

Astra → Astra → Astra → Astra

With Save My Astra:

使用 Save My Astra 后：

```text
Astra 🧠
 ├─ Luna ⚡
 ├─ Luna ⚡
 └─ Terra ⚡
```

Luna is the default worker. Terra is the fallback if Luna cannot spawn.

Luna 是默认 worker；如果 Luna 无法 spawn，则回退到 Terra。

**Premium intelligence for premium decisions.**

**重要决策用高端模型，其余交给 worker。**

Default profile `astra-luna` pins `gpt-6-astra` (`low`) as the parent and `gpt-5.6-luna` (`max`) as the child. This is a hypothesis to install and measure, not a claim that the pairing is cheapest.

默认 profile `astra-luna` 将 `gpt-6-astra`（`low`）固定为 parent、`gpt-5.6-luna`（`max`）固定为 child。这是一个可安装、可测量的假设，并不声称该组合成本最低。

## Install / 安装

Does not overwrite `model_provider`, MCP servers, notify paths, or plugins. Existing `config.toml` and `AGENTS.md` are copied to `~/.codex/backups/` first.

不会覆盖 `model_provider`、MCP servers、notify 路径或 plugins。现有的 `config.toml` 和 `AGENTS.md` 会先备份到 `~/.codex/backups/`。

```bash
./scripts/install.sh                 # default profile: astra-luna
./scripts/install.sh --profile astra-terra
./scripts/install.sh --dry-run
python -m eval verify --profile astra-luna
```

Or tell Codex: install Save My Astra from this repo. The skill is `skills/save-my-astra/SKILL.md`.

也可以直接告诉 Codex：从本仓库安装 Save My Astra。Skill 位于 `skills/save-my-astra/SKILL.md`。

Then start a **new** Codex session. Spawn one read-only repo scan. The child must be `gpt-5.6-luna` (or Terra if you installed that profile), not `gpt-6-astra`.

然后开启一个**新的** Codex 会话，spawn 一次只读仓库扫描。子 agent 必须是 `gpt-5.6-luna`（若安装的是 Terra profile 则为 Terra），不能是 `gpt-6-astra`。

| Profile | Parent | Child |
| --- | --- | --- |
| `astra-luna` | `gpt-6-astra` / `low` | `gpt-5.6-luna` / `max` |
| `astra-terra` | `gpt-6-astra` / `low` | `gpt-5.6-terra` / `max` |
| `astra-solo` | `gpt-6-astra` / `low` | off (baseline) |
| `sol-luna` | `gpt-5.6-sol` / `low` | `gpt-5.6-luna` / `max` |

Use `astra-terra` when Luna is missing from the spawn allowlist.

当 spawn 允许列表里没有 Luna 时，使用 `astra-terra`。

## What this writes / 会写入什么

- `~/.codex/config.toml` — parent model, `[agents]` defaults, `hide_spawn_agent_metadata = false`
- `~/.codex/agents/luna-max-worker.toml` — child model pinned
- `~/.codex/AGENTS.md` — delegate when it can save time or quality; do not clone the parent
- `~/.codex/skills/save-my-astra/SKILL.md` — the same one-liner for later sessions

- `~/.codex/config.toml` — parent 模型、`[agents]` 默认值、`hide_spawn_agent_metadata = false`
- `~/.codex/agents/luna-max-worker.toml` — 固定 child 模型
- `~/.codex/AGENTS.md` — 在能节省时间或提升质量时委派；不要克隆 parent
- `~/.codex/skills/save-my-astra/SKILL.md` — 供后续会话复用的同一套说明

Plus quota is not API spend. Subagent runs can use more tokens and still cost less if cheap tokens replace expensive ones.

Plus 额度不等于 API 花费。子 agent 可能消耗更多 token，但只要用便宜 token 替换昂贵 token，整体仍可能更省。

## Measure (optional) / 测量（可选）

Eval adapters live under `eval/`. Method and claim rules: [docs/method.md](docs/method.md).

Eval 适配器位于 `eval/`。方法与结论规则见 [docs/method.md](docs/method.md)。

```bash
python -m pip install -e ".[dev]"
python -m eval check
```

## Contributing / 贡献

Fork, branch, PR, squash merge. Details: [CONTRIBUTING.md](CONTRIBUTING.md).

Fork、开分支、PR、squash 合并。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
