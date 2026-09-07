# Method

Daily install is in the [README](../README.md). This page is how to measure routing, not how to use it.

Routing is a hypothesis. Measure it. Do not write “this is the cheapest Codex setup.”

## Why three suites

**Terminal-Bench 4.0 (capability).** Real terminals, 66 tasks, official Harbor harness, first-class `--agent codex`. OpenAI reported GPT-6 Astra at 57.9% on this set. The public board already lists Codex + Sol / Terra / Luna with resolve rate, tokens, and cost. Pin `terminal-bench/terminal-bench@4.0.0`. Quality = official resolution rate. Tokens and dollars are a contrast, not a clean cost instrument (sandbox noise).

**SWE-bench Pro (capability contrast).** Issue → PR on large repos. Official Docker + `swe_bench_pro_eval.py`. Default smoke 5–10 IDs after download. Full public set (731) is `--slice full`. Does not replace TB 4.0.

**MATH-500 / AIME 2025 (cost calibration).** Almost no Docker / repo / environment noise. Boxed answers. Cheap repeats. Use them to read:

- Astra-solo: parent tokens / $X
- Astra-Luna: parent tokens + child tokens / $Y

Accuracy is a guardrail (did cheaper tokens smash the answer?), not a math SOTA and not coding evidence. AIME items rarely have enough parallel work to force a spawn.

**FrontierMath public-12.** Same calibration role. Only the 12 public problems. Not the private board.

## Arms

Same items, same grader.

1. `astra-solo` — parent finishes the item.
2. Chosen profile — default `astra-luna`.

SWE / TB default `n=1`. Math default `n=3`.

## Delegation

`spawn=0` is not a failure.

- `delegation_expected=true` and `spawn=0` → `routing_missed`
- `delegation_expected=false` and `spawn>0` → `over_delegated`
- everything else, including `optional` + `spawn=0` → `routing_ok`

Eval `AGENTS.md` may say: plan, then optionally send compute / check to the child. It must not coerce spawn. Daily install copy also avoids “delegate immediately.”

## Runtimes

- **API** (`--runtime api`): OpenAI Responses API. Reproducible token totals for cost calibration.
- **Codex CLI** (`--runtime codex`): isolated `CODEX_HOME=.eval-codex/<run>`, `codex exec --json`, parse `turn.completed.usage`.
- **Harbor** (`--runtime harbor`): official TB path.

```bash
harbor run -d terminal-bench/terminal-bench@4.0.0 \
  --agent codex \
  --model openai/gpt-6-astra \
  --ak config=<rendered.toml>
```

Harbor accepts Codex native config via `--ak config=...`. Rendered `AGENTS.md` and worker toml live next to that config.

Plus allowance is not API spend. Multi-agent runs can use more tokens and still cost less if cheap tokens replace expensive ones.

## Process this repo encodes

1. Plus Astra lives in Work and Codex, not the ordinary Chat picker.
2. Astra Light (`low`) orchestrates. Luna Max (or Terra Max) does bounded work.
3. Standing `AGENTS.md` is how you stop pasting a session opener.
4. Official line to keep: if work can be parallelized by delegating, use collaboration tools when that could save time or improve quality.
5. Measure before claiming savings.

Prices in `eval/pricing.yaml` are editable estimates. Replace them with the bill you actually pay.
