# Contributing

Thanks for helping improve Save My Astra.

感谢你愿意改进 Save My Astra。

This repo is small on purpose: install scripts, Codex templates, a skill, and optional eval adapters. Most contributions are docs, routing copy, or measured claims — not a large app.

这个仓库刻意保持精简：安装脚本、Codex 模板、一个 skill，以及可选的 eval 适配器。多数贡献是文档、路由文案，或有测量支撑的结论——不是大型应用。

## Before you start / 开始之前

- Read the [README](README.md) for install and verify steps.
- If you change eval claims or suites, read [docs/method.md](docs/method.md).
- User-facing docs (README, SKILL, this file) stay **bilingual**: English paragraph, then Chinese. Do not split into separate EN/ZH files.

- 先读 [README](README.md) 了解安装与验证步骤。
- 若改动 eval 结论或 suite，先读 [docs/method.md](docs/method.md)。
- 面向用户的文档（README、SKILL、本文件）保持**中英对照**：英文段落后紧跟中文，不要拆成独立的 EN/ZH 文件。

## How to contribute / 如何贡献

`main` is protected. Do not push to it directly.

`main` 已受保护，不要直接 push。

```text
fork or clone
  -> branch from origin/main (feat/... or docs/...)
  -> edit locally and run checks
  -> open a PR targeting main
  -> squash merge (merge commits are blocked)
```

```text
fork 或 clone
  -> 从 origin/main 开分支（feat/... 或 docs/...）
  -> 本地修改并跑检查
  -> 开 PR 指向 main
  -> squash 合并（禁止 merge commit）
```

### Branch and PR / 分支与 PR

1. Sync with `main`:

   ```bash
   git fetch origin
   git checkout main
   git pull --ff-only origin main
   git checkout -b docs/your-change   # or feat/your-change
   ```

   与 `main` 同步：

   ```bash
   git fetch origin
   git checkout main
   git pull --ff-only origin main
   git checkout -b docs/your-change   # 或 feat/your-change
   ```

2. Push your branch and open a PR to `main`:

   ```bash
   git push -u origin HEAD
   gh pr create --base main --title "your title" --body "what changed and why"
   ```

   推送分支并开 PR 到 `main`：

   ```bash
   git push -u origin HEAD
   gh pr create --base main --title "your title" --body "改了什么、为什么"
   ```

3. Merge with **Squash and merge** or **Rebase and merge**. **Create a merge commit** is disabled on `main`.

   使用 **Squash and merge** 或 **Rebase and merge** 合并。**Create a merge commit** 在 `main` 上已禁用。

4. Do not `git push --force` to `main`. Do not delete `main`.

   不要对 `main` 执行 `git push --force`，也不要删除 `main`。

Approval count is 0 for this repo — maintainers can squash their own PRs after checks pass.

本仓库审批人数为 0——maintainer 在检查通过后可以 squash 自己的 PR。

## Local setup and checks / 本地环境与检查

Requires Python 3.11+.

需要 Python 3.11+。

```bash
python -m pip install -e ".[dev]"
python -m eval check
pytest
```

All three should pass before you open a PR.

开 PR 前三项都应通过。

| Command | Purpose |
| --- | --- |
| `python -m eval check` | Templates, profiles, skill needles, routing rules |
| `pytest` | Install merge, render, routing unit tests |
| `python -m eval verify --profile astra-luna` | Optional after install-script changes; needs a real `~/.codex` |

| 命令 | 作用 |
| --- | --- |
| `python -m eval check` | 模板、profile、skill 关键字、路由规则 |
| `pytest` | 安装合并、渲染、路由单元测试 |
| `python -m eval verify --profile astra-luna` | 可选；改了安装脚本后跑；需要本机 `~/.codex` |

Full benchmarks (Terminal-Bench, SWE-bench Pro, math calibration) are documented in [docs/method.md](docs/method.md). You do not need to run them for doc-only PRs.

完整 benchmark（Terminal-Bench、SWE-bench Pro、数学校准）见 [docs/method.md](docs/method.md)。仅文档 PR 不必跑它们。

## What to change where / 改什么看哪里

| Area | Paths |
| --- | --- |
| User docs | [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md) |
| Codex skill | [skills/save-my-astra/SKILL.md](skills/save-my-astra/SKILL.md) |
| Daily / eval agent copy | [templates/AGENTS.md](templates/AGENTS.md), [templates/AGENTS.eval.md](templates/AGENTS.eval.md) |
| Profiles and snippets | `profiles/`, [templates/config.snippet.toml](templates/config.snippet.toml), [templates/agents/worker.toml](templates/agents/worker.toml) |
| Install | [scripts/install.sh](scripts/install.sh), [eval/install_merge.py](eval/install_merge.py) |
| Eval adapters | [eval/](eval/) |

| 领域 | 路径 |
| --- | --- |
| 用户文档 | [README.md](README.md)、[CONTRIBUTING.md](CONTRIBUTING.md) |
| Codex skill | [skills/save-my-astra/SKILL.md](skills/save-my-astra/SKILL.md) |
| 日常 / eval agent 文案 | [templates/AGENTS.md](templates/AGENTS.md)、[templates/AGENTS.eval.md](templates/AGENTS.eval.md) |
| Profile 与片段 | `profiles/`、[templates/config.snippet.toml](templates/config.snippet.toml)、[templates/agents/worker.toml](templates/agents/worker.toml) |
| 安装 | [scripts/install.sh](scripts/install.sh)、[eval/install_merge.py](eval/install_merge.py) |
| Eval 适配器 | [eval/](eval/) |

## Project rules (easy to break) / 项目规则（容易踩坑）

These are enforced by [eval/check.py](eval/check.py) and review. Summarized here so PRs do not bounce.

这些由 [eval/check.py](eval/check.py) 和 review 把关。在此摘要，避免 PR 被退回。

### Claims / 结论表述

- Do **not** call this “the cheapest Codex setup” or “最便宜”.
- Routing is a **hypothesis**: install, measure, then argue. See [docs/method.md](docs/method.md).

- **不要**称这是“最便宜的 Codex 配置”或 “最便宜”。
- 路由是**假设**：先安装、测量，再下结论。见 [docs/method.md](docs/method.md)。

### Agent copy / Agent 文案

- Daily [templates/AGENTS.md](templates/AGENTS.md) must not coerce spawn (`must immediately`, `立刻派`, etc.).
- Eval [templates/AGENTS.eval.md](templates/AGENTS.eval.md) may allow optional delegation; it must not force spawn.

- 日常 [templates/AGENTS.md](templates/AGENTS.md) 不能强迫立刻 spawn（`must immediately`、`立刻派` 等）。
- Eval [templates/AGENTS.eval.md](templates/AGENTS.eval.md) 可以允许可选委派，但不能强迫 spawn。

### Skill needles / Skill 必含关键字

If you edit [skills/save-my-astra/SKILL.md](skills/save-my-astra/SKILL.md), keep at least:

若修改 [skills/save-my-astra/SKILL.md](skills/save-my-astra/SKILL.md)，至少保留：

- `name: save-my-astra`
- `default_subagent_model`
- `gpt-6-astra`
- `gpt-5.6-luna`
- `hide_spawn_agent_metadata`

### Install merge / 安装合并

Install merges into `~/.codex`; it must **not** wipe:

安装会合并进 `~/.codex`；**不得**覆盖：

- `model_provider`
- MCP server blocks
- root-level `notify`
- plugins

Existing `config.toml` and `AGENTS.md` are backed up under `~/.codex/backups/` first. Merge snippets; do not replace whole files.

现有的 `config.toml` 和 `AGENTS.md` 会先备份到 `~/.codex/backups/`。用片段合并，不要整文件替换。

### Profiles / Profile

Child model must not equal parent model on worker profiles (`astra-luna`, `astra-terra`).

Worker profile 的 child 模型不能等于 parent 模型（`astra-luna`、`astra-terra`）。

## Questions / 问题

Open a [GitHub issue](https://github.com/fengxiaohu/save-my-astra/issues) with repro steps or a small PR draft if you are unsure.

不确定时，开 [GitHub issue](https://github.com/fengxiaohu/save-my-astra/issues) 说明复现步骤，或先发一个小 PR 草稿。
