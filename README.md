# Phylogenetic Harness

An **auditable, sceptical** core for AI-assisted phylogenomics. The point is not
to run pipelines that look successful — it is to make it **impossible for an AI
to produce false science behind a green checkmark**.

> Guiding invariant: *a result that ran is not a result that is true.*
> Technical state and scientific state are tracked **separately** and never
> auto-derived from one another.

## Status — v1 (auditable core)

This milestone delivers the auditable spine with real, passing tests. It does
**not** yet execute heavy bioinformatics pipelines (Cactus / IQ-TREE / large
alignments); those tools are *registered as contracts* and executed on demand in
later milestones.

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
