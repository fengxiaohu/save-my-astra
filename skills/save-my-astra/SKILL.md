---
name: save-my-astra
description: >-
  Install Save My Astra Codex routing: GPT-6 Astra as the root agent,
  gpt-5.6-luna (or terra) as a pinned subagent. Use when the user wants
  to save GPT-6 Astra tokens, cheaper Codex subagents,
  default_subagent_model, or to stop children inheriting gpt-6-astra.
  安装 Save My Astra Codex 路由：GPT-6 Astra 作为根 agent，gpt-5.6-luna（或 terra）
  作为固定的子 agent。当用户想节省 GPT-6 Astra 额度、使用更便宜的 Codex 子 agent、
  配置 default_subagent_model，或阻止子 agent 继承 gpt-6-astra 时使用。
---

# Save My Astra

Stop burning GPT-6 Astra tokens on work Luna can do.

别让 Luna 能干的活继续烧 GPT-6 Astra 的额度。

Pin GPT-6 Astra as the parent. Pin a cheaper child so spawned agents do not clone Astra.

将 GPT-6 Astra 固定为 parent，并固定一个更便宜的 child，避免 spawn 出来的 agent 克隆 Astra。

Do not call this the lowest-cost Codex setup. Subagents often use more tokens; they cost less only when cheap tokens replace expensive ones.

不要称这是成本最低的 Codex 配置。子 agent 往往消耗更多 token；只有用便宜 token 替换昂贵 token 时，整体才会更省。

## Install / 安装

From this repo, merge into `~/.codex` without touching `model_provider`, MCP servers, `notify`, or plugins:

从本仓库合并到 `~/.codex`，不要改动 `model_provider`、MCP servers、`notify` 或 plugins：

```bash
./scripts/install.sh                 # default: astra-luna
./scripts/install.sh --profile astra-terra
./scripts/install.sh --dry-run
python -m eval verify --profile astra-luna
```

If the repo is not checked out, apply the same three writes:

如果仓库未 checkout，手动执行同样的三处写入：

1. `~/.codex/config.toml`
   - `model = "gpt-6-astra"`
   - `model_reasoning_effort = "low"`
   - `[agents] enabled = true`
   - `default_subagent_model = "gpt-5.6-luna"`
   - `default_subagent_reasoning_effort = "max"`
   - `[features.multi_agent_v2] hide_spawn_agent_metadata = false`
2. `~/.codex/agents/luna-max-worker.toml` with `model = "gpt-5.6-luna"` (do not omit `model`)
3. `~/.codex/AGENTS.md`: delegate when it can save time or quality; `fork_turns: none`; do not spawn another Astra

1. `~/.codex/config.toml`
   - `model = "gpt-6-astra"`
   - `model_reasoning_effort = "low"`
   - `[agents] enabled = true`
   - `default_subagent_model = "gpt-5.6-luna"`
   - `default_subagent_reasoning_effort = "max"`
   - `[features.multi_agent_v2] hide_spawn_agent_metadata = false`
2. `~/.codex/agents/luna-max-worker.toml`，其中 `model = "gpt-5.6-luna"`（不要省略 `model`）
3. `~/.codex/AGENTS.md`：在能节省时间或提升质量时委派；`fork_turns: none`；不要再 spawn 另一个 Astra

Backup the existing `config.toml` and `AGENTS.md` first. Merge; do not replace the whole file.

先备份现有的 `config.toml` 和 `AGENTS.md`。采用合并方式，不要整文件替换。

Luna spawn allowlists sometimes reject the child. Retry once with `astra-terra` (`gpt-5.6-terra` / `max`) and say so.

Luna 的 spawn 允许列表有时会拒绝 child。可改用 `astra-terra`（`gpt-5.6-terra` / `max`）重试一次，并告知用户。

## Verify / 验证

Start a **new** Codex session. Ask it to spawn one read-only repo scan. The child model must be `gpt-5.6-luna` or `gpt-5.6-terra`, never `gpt-6-astra`.

开启一个**新的** Codex 会话，让它 spawn 一次只读仓库扫描。子 agent 模型必须是 `gpt-5.6-luna` 或 `gpt-5.6-terra`，绝不能是 `gpt-6-astra`。

ChatGPT-login sessions may ignore per-spawn `model`. Defaults in `config.toml` plus the worker file are the reliable path.

ChatGPT 登录会话可能会忽略单次 spawn 的 `model`。可靠做法是依赖 `config.toml` 中的默认值加上 worker 文件。
