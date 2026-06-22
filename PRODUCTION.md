# Production operation & the five guarantees

This harness exists to stop an AI/agent from producing **false science behind a
successful-looking pipeline**. Core invariant: **"it ran" ≠ "it is true."**
TechnicalState and ScientificState are separate enums and are never auto-derived
from each other.

The production hardening (iterations 1–10) makes five operational guarantees
**real and enforced**, not aspirational. Each is backed by code and a test.

---

## 1. Bitácora completa — complete audit log

Every agent action is logged persistently with a timestamp: what it did, the
command/call executed, the input received, the output returned, duration, and
whether it failed.

- **Where:** `~/.harness/audit.jsonl` (override with `HARNESS_AUDIT_LOG`).
- **Append-only & non-overwritable:** each record carries `prev` = sha256 of the
  previous line — a **hash chain**. Any edit or deletion breaks the chain.
- **Per-action detail:** `action_finished` records argv, inputs, outputs,
  exit_code, wall_seconds, ok. `tool_call` records every tool invocation and
  whether it was `in_harness`.
- **Verify:** `harness audit --verify` → `{ok, records, broken_at}`.

```
harness audit            # operator view: runs + tool calls, machine-wide
harness audit --full     # every record
harness audit --verify   # check the tamper-evident hash chain
```

## 2. Reporte automático — automatic report

On finish/fail the harness writes a summary plus any anomaly detected.

- `Run.finish()` calls `autoreport.generate()` → **RUN_SUMMARY.json**,
  **ANOMALIES.json**, and **ALERT.txt** in the run directory.
- Anomaly kinds: failed/degenerate/timeout/disk_abort/validation_failed/
  orphan_task/out_of_harness_tool/audit_chain_broken.
- Anomalies are also pushed to the central audit log (`run_anomalies`).

## 3. Contención — containment

Everything runs **inside** the harness; nothing executes without passing through
it. We never assume the agent's good faith.

- **Single execution path:** `TaskRunner.run_task` (approval gate → registry gate
  → state machine → argv-only execution, `shell=False` → validators → scientific
  evidence → terminal state).
- **Sandbox:** tool execution wrapped in `bwrap`/`apptainer` (default-on in the
  pipeline when a backend exists), read-only filesystem + no network except the
  run's own workspace.
- **Kill-switch:** `harness kill <run_dir>` (per-run STOP) and
  `harness kill --panic` (global STOP). The runner checks before every task and
  aborts cleanly — no execution, no crash.
- **Action allowlist:** a run may declare `tool_allowlist`; any tool not on it is
  refused on top of the registry gate.
- **Resource limits:** opt-in `RLIMIT_CPU`/`RLIMIT_AS` backstop on the child
  (off by default so multithreaded JVM tools like IQ-TREE aren't falsely killed).
- **Strict policy:** `strict=True` (default in `pipeline`/`genome-phylo`) **blocks
  a non-compliant run up front** — no sandbox, network allowed, or non-strict
  approval → `PolicyViolation`, nothing runs.

```
harness kill runs/<run_id>     # stop one run at its next task
harness kill --panic           # stop ALL runs
```

## 4. Trazabilidad — traceability

Every execution has a unique `run_id` and can be reconstructed exactly.

- `harness trace <run_id|run_dir>` merges the three independent records — the
  central **audit log**, the per-run **event store** (state machine), and the
  **result bundles** (verdicts) — into one timestamp-ordered timeline.
- Because the three sources are independent, a disagreement between them is
  itself visible in the trace.
- `harness runs` catalogues every run + its outcome across the machine.

```
harness trace <run_id>          # full reconstructed timeline
harness trace <run_id> --json   # raw merged timeline
```

## 5. Robustez — robustness

An agent failure must **never** take down the harness.

- **Exception frontier:** `run_task` wraps execution in try/except/finally — a
  validator/science exception yields `FAILED_FATAL`, releases the lease, and
  persists a partial bundle. It never propagates.
- **Real retries:** failed-retryable execution re-runs up to `max_retries` with
  per-attempt logs, then `FAILED_FATAL`.
- **Fault isolation:** the `Scheduler` runs a grid; one failing cell does not stop
  the others.
- **Crash recovery:** `harness resume <run_dir>` rehydrates state from the event
  log, reaps orphans (no zombies), and finishes only the unfinished tasks.
- **Crash-loop quarantine:** the `Supervisor` writes an in-flight heartbeat; a
  poison task that hard-crashes the *process* gets a strike on each resume and is
  **QUARANTINED** after `max_strikes`, so resume can never enter an infinite loop.

```
harness resume runs/<run_id>    # finish a crashed run; quarantine poison tasks
```

---

## CI gate

`bash scripts/ci.sh` runs ruff + mypy + the full pytest suite. Production changes
must keep it green.

## Data safety

External genomes (e.g. `HomoPan_ancestor/genomes/`, `results/ancestors/`) are
**read-only and irreplaceable**. The harness writes only under its own
`runs/{run_id}/` workspace and never modifies, moves, or overwrites source data.

## Scientific guardrails (why the harness exists)

- **Observed-taxa-only:** reconstructed / non-reproducible genomes (e.g. Cactus
  ancestors that differ ~0.33 identity between runs) are **diagnostic-only**,
  never phylogenetic evidence — they must not be tree tips.
- **Locus-independence gate:** ASTRAL is invalid on linked loci; the run must
  assert independence.
- **Supported-conflict discordance:** distinguishes real ILS (well-supported
  conflict) from raw RF noise.
