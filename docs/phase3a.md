# Phase 3A implementation and operating boundary

Phase 3A protocol version **1.2** uses **Astra medium** in both arms, with Luna max only in treatment.
The shared daily profiles remain unchanged. Experimental configuration is rendered
by `eval.phase3_config`; it disables Terra fallback and omits a baseline worker.

The experiment follows [PHASE3A_PLAN.md](../PHASE3A_PLAN.md). This implementation
does not make a static configuration check equivalent to a live Gate 0 probe.
At this snapshot `formal_trials=0`: the authorized live Gate 0 probe is in
progress, with no Gate 0 check recorded as passed and no formal model run
claimed. Offline tests do not start a Harbor trial, container, model, or API.

## Entry points

`python -m eval phase3a --help` describes three separate operations:

- `prepare --tasks PATH --codex-version EXACT_VERSION --out PATH`: freezes an
  existing local snapshot of the four tasks and the implementation. Does not call
  a Harbor trial, container, model, or API, and does not download tasks. It may
  read installed command versions using `--version`.
- `inspect --out PATH`: checks the frozen inputs and references to Gate 0 evidence.
  It does not perform the probes represented by that evidence.
- `execute --out PATH --telemetry PATH --auth-source PATH`: **starts real runs**.
  Use only after the separately authorized Gate 0 has passed. This command was
  not run during implementation.

The old `run --runtime harbor` path is now command-only. It cannot silently execute
only treatment. Terminal-Bench cannot run through the generic API or Codex prompt
adapter. `--slice phase3a` selects exactly four task IDs and rejects truncation.

## Frozen inputs

The task root must contain these four real, unmodified task directories:

1. `session-window-debug`
2. `payments-pipeline-fix`
3. `bun-sourcemap-leak`
4. `nextjs-performance`

Each needs its original instruction, task configuration, environment and verifier.
Preparation checks and hashes the local inputs; it does not establish their upstream
authenticity or M1 compatibility. Gate 0 supplies that evidence. Record container
image digests and effective VM/container resource limits in the environment evidence.
Do not use the synthetic task fixtures from unit tests as benchmark inputs.

Preparation also records dependency/source evidence. Any later change invalidates
the freeze. Use a new output directory and repeat the relevant Gate 0 checks after
changing code, task files, policies or dependencies. Do not combine protocol versions.
The upstream task source is pinned to `harbor-framework/terminal-bench` v4.0.0 at
commit `452bf305c6daa62fc59061d22133a7cbc7c1572e`; this reference still requires
Gate 0 verification against the frozen local task snapshot.

## Gate 0 evidence

Create `preflight/gate0.json` only from actual, separately run preflight observations.
It contains `freeze_sha256` from `manifest.json`, `mode` (`full` or `routing-only`),
and `checks`. Each check has `passed: true` only after verification, plus an
`artifacts` list of `{ "path": "relative/path", "sha256": "actual file hash" }`.
Paths must stay inside `preflight/`; hashes must match the saved files.
The accepted Gate 0 document is also hashed into runner state on first use;
changing mode or evidence after that stops the protocol.

`gate0.json` also requires a canonical `environments` mapping containing all four
task IDs. Each task uses role-separated `agent` and, when the verifier is
separate, `verifier` objects. Each role pins every service image and its
`cpus`/`memory_mb` resources. Images may be immutable
`registry/image@sha256:...` references or local `sha256:<64-hex>` IDs; local IDs
are checked with `docker image inspect` and must resolve to the exact frozen ID.
The adapter inspects the canonical role shape and service set before applying it
to Harbor and the Compose overlay, so a separate verifier cannot inherit the
agent image or resources. Resolve Compose includes/extends before freezing;
unresolved service graphs are rejected. The authorized VM allocation is 6 vCPU
and 16 GiB; Gate 0 must record and verify the effective cap. Do not invent image
digests or passed evidence in this file. The validator retains a flat
agent-equivalent shape only for tasks whose verifier is not separate; Gate 0
artifacts should use the canonical role mapping.

Required check names are:

- `task_environments`: all four original tasks, pinned images, reset policy, VM
  limits, architecture and verifier availability.
- `chatgpt_auth`: the actual container agent uses ChatGPT authentication.
- `effective_models_and_policy`: root model/effort, worker effort, no fallback,
  concurrent-child limit and no recursive delegation actually apply.
- `baseline_agents_disabled`: baseline cannot create children.
- `luna_runtime_identity`: real child creation and runtime model attribution.
- `verifier_collection`: original verifier results and termination states survive
  both success and failure.
- `container_cleanup`: owned agents and containers stop on timeout/interruption.
- `quota_and_resource_source`: the telemetry producer is live and observes the
  correct quota bucket and host resource pressure.
- `usage_semantics`: required for `full`; covers event coverage, cumulative versus
  incremental usage, root/child separation and included cached/reasoning fields.

The validator checks binding and integrity of evidence, not whether a human's
claim is true. Do not generate affirmative evidence from the offline test suite.
`routing-only` does not permit claims about complete token totals or savings.

## Quota and resource telemetry

The runner consumes an external JSON snapshot, written atomically by
`eval.phase3_observer`, the chosen Gate 0-verified observer. The observer reads
the real Codex primary quota window of 300 minutes and the weekly secondary
window of 10080 minutes, and validates both window durations before writing a
sample. It never treats missing telemetry as zero:

```json
{
  "observed_at": 0,
  "used_percent": 0,
  "resets_at": 0,
  "limit_id": "actual-account-limit-id",
  "weekly_used_percent": 0,
  "weekly_resets_at": 0,
  "resource_pressure": "normal"
}
```

The zero timestamps above are placeholders and are rejected. Times are Unix
seconds. `resource_pressure` must be `normal` or `stop`. Refresh at least every
60 seconds; samples older than 120 seconds are rejected. The runner checks about
every five seconds while running. It blocks new runs at +20 percentage points and
interrupts at +30. Both primary and weekly exhaustion can pause the protocol;
weekly pauses use the 10080-minute reset timestamp. Window resets are never
subtracted across; decreasing readings or changed bucket IDs require attention.
Quota remains an account-level observation, not benchmark-attributed savings. No
automatic quota API integration is implied.

The baseline and observed snapshots are persisted so a restart cannot reset the
stop-loss within the same quota window. A `used_percent` reading of 100 is an
exhausted bucket even when its delta is below 30 points. Soft and hard quota
pauses retain the original `resets_at`; the runner waits for a fresh sample
from a later, usable window before launching again. It does not use a same-day
marker as a substitute for observing that reset.

A pause before launching leaves `next_index` unchanged. If quota interrupts an
active trial, that trial is retained as a partial `budget_aborted` record with
`verifier.passed: null`, and the next invocation starts the next planned trial
with a new process and attempt ID. It never resumes the interrupted Codex
session or silently reruns that attempt. Resource, protocol, telemetry, and
other permanent failures remain stopped until a new protocol decision; only a
quota pause can resume under the same freeze.

## Records and execution

Only one runner can own a protocol. The eight steps are A/B, B/A, A/B, B/A over
T1–T4. Gate 1 runs after the first four completed attempts. Independent infrastructure
failures permit at most one explicit linked replacement; quality failures and missed
delegation are never retried for a better score. If quota telemetry stops the run
between an infrastructure failure and its replacement, the runner records
`manual_review_required` and stops permanently; a later invocation cannot reset the
replacement count by replaying the primary attempt.

Each attempt has a separate configuration, task trial and artifact directory.
Authentication is staged outside reports with private permissions and removed on
success or failure. API-key environment variables and custom OpenAI API endpoints
are removed from the experiment process environment. Raw output is redacted, and
accidental auth artifacts are removed before final reporting.
SIGTERM unwinds cleanup. An uncatchable SIGKILL or host crash can defer temporary
credential cleanup until the next invocation; a private ownership lease permits
removing only the marked directory belonging to the dead runner. Interrupted runs
are retained as incomplete rather than silently resumed.

The agent budget is at most 60 minutes (shorter original task limits win). Setup and
verifier have separate bounded phases; the host supervisor adds an outer deadline.
An outer kill or quota stop may prevent final verification; such a result remains
aborted/incomplete with `passed: null`, never a fabricated pass or ordinary failure.
Gate 0 must validate cleanup on the actual Docker backend before formal runs.

`records.json` preserves every attempt; `summary.json` and `REPORT.md` show all four
task pairs, including not-run/missing results, model identities, parent reductions,
coverage and timings. Phase 3A USD estimates are always null. Unknown model identity
and incomplete usage stay unknown; they cannot establish zero clones or savings.

Native session metadata determines thread ancestry. A native Codex `turn_context`
provides the CLI-resolved model/effort for that thread; this is recorded with its
source file and line, separately from requested spawn parameters. It is not an
independent server-side attestation. Collection completeness requires the adapter's
successful post-stop snapshot marker; a `turn.completed` event alone is insufficient.

The Harbor bridge uses the upstream `JobConfig`, `AgentConfig`, `VerifierConfig`,
installed Codex and Docker environment interfaces. It checks required schema fields
before a task can run. The live Harbor/Codex version combination must still pass
Gate 0; current Harbor may require a newer Python than the Python 3.11 used by the
offline tests. No Harbor installation or compatible runtime is implied by a passing
unit test.

Formal status remains `formal_trials=0` while the real Gate 0 probe is in progress.
No check is marked passed from static inspection or offline fixtures; the first
formal trial remains blocked until the bound Gate 0 evidence is complete.

## Offline validation

The added tests use synthetic events, task fixtures, mocked Harbor execution and
short local Python subprocesses. They cover configuration isolation, manifest
tampering, gate behavior, ordered scheduling, quota boundaries, credential cleanup,
process interruption and report/measurement semantics. They do not consume model
quota or establish live Harbor/CLI compatibility.
