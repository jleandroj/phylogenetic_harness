# Phylogenetic Harness

An **auditable, sceptical** core for AI-assisted phylogenomics. The point is not
to run pipelines that look successful — it is to make it **impossible for an AI
to produce false science behind a green checkmark**.

> Guiding invariant: *a result that ran is not a result that is true.*
> Technical state and scientific state are tracked **separately** and never
> auto-derived from one another.

## Status — v1 core + P0/P1 audit hardening

This milestone delivers the auditable spine with real, passing tests (113), then
closes the P0/P1 findings from an adversarial self-audit. It does **not** yet
execute heavy bioinformatics pipelines (Cactus / IQ-TREE / large alignments);
those tools are *registered as contracts* and executed on demand in later
milestones.

### The single execution path

Nothing executes a command except `harness.runner.TaskRunner`, which forces, in
order: **approval gate → tool-registry gate → APPROVED/LEASED/RUNNING → argv-only
execution → all declared validators → scientific interpretation → terminal state**.
A green exit code never auto-promotes to a biological conclusion — the scientific
state is assigned only by `harness.science`.

### Audit hardening (verified by tests)

- **argv-only execution** — no shell; hostile params are inert (`test_injection`).
- **real recovery** — state is rebuilt from the event log; a real `SIGKILL`
  mid-run leaves no zombie (`test_process_kill_real`).
- **multi-process event store** — `flock` + global sequence; 8 concurrent writers
  don't corrupt the log (`test_concurrency_real`).
- **output caps** — oversized stdout is truncated, not allowed to fill the disk
  (`test_stdout_giant`); a pre-flight disk check aborts when nearly full.
- **per-PID resource sampling**, **per-attempt logs**, **secret redaction**,
  **deterministic seeds + `TOOLS.lock.json`**, and a stricter
  `BIOLOGICALLY_INTERPRETABLE` bar (≥2 independent statistical evidences).

## Layout

```
harness/        the package (one module per spec §24 contract)
tools/          tool contracts (samtools, bcftools, mafft, iqtree2 ...)
schemas/        example dataset manifest (spec §24.1)
tests/          pytest suite — every test pins a spec invariant
runs/           per-run output (gitignored)
```

### Core modules

| Module | Role |
|---|---|
| `states` | separate `TechnicalState` / `ScientificState` enums + legal-transition guard |
| `events` | append-only JSONL event store, closed event vocabulary |
| `logging_json` | structured JSON logger (never `print`) |
| `seeds` | deterministic seeds; rejects `True`/`False`/`None` |
| `hardware` / `environment` | real CPU/RAM/disk/GPU probe + 4 `ENVIRONMENT.*` artefacts |
| `resources` | per-execution CPU/RAM/disk; flags `RESOURCE_AUDIT_INCOMPLETE` |
| `tools` / `datasets` / `tasks` | tool, dataset-manifest and task contracts |
| `approval` | approval gate (auto-flags GPU / big-RAM / long jobs) |
| `executor` | LocalExecutor (captures stdout/stderr to files); DryRun/AuditOnly; SLURM/GPU stubs |
| `validators` | FASTA / Newick / VCF / file validators (technical only) |
| `science` | 3-level interpretation, negative-result + degeneracy classification |
| `leases` / `workers` | lease expiry, reaping, requeue — no zombie tasks |
| `recovery` | rebuild task state from the event log; detect orphans |
| `runner` | **TaskRunner** — the single enforced execution path |
| `redaction` | mask secrets in captured output and the env snapshot |
| `report` | final report with the 13 mandatory sections (spec §24.14) |
| `run` | frozen `RunConfig` + run orchestration |

## Usage

```bash
# capture the real environment (writes 4 ENVIRONMENT.* artefacts)
python -m harness capture-env --out runs/env_snapshot

# validate + checksum a dataset manifest
python -m harness validate-manifest schemas/dataset.manifest.example.yaml

# end-to-end auditable demo run -> runs/<run_id>/report.md
python -m harness demo-run

# tests
pytest -q
```

## What this harness refuses to do

- Treat exit code 0 as biological truth.
- Mark a negative result as failure without classifying *why* (8 categories).
- Report a degenerate output (empty, constant metric, all-identical) as success.
- Run a bioinformatics tool that is not a registered contract.
- Run an analysis without a dataset manifest.
- Run a flagged task (GPU / >50% RAM / long walltime / overwrite) without approval.
- Claim a single "true" tree, or that an inferred ancestor is a real individual.
