# Quickstart

An **auditable, sceptical** harness for AI-assisted phylogenomics. Its job is not
to run pipelines that look successful — it is to make it **impossible to produce
false science behind a green checkmark**.

> Core invariant (enforced in code + tests): *a result that ran is not a result
> that is true.* Technical state and scientific state are tracked separately and
> never auto-derived from one another.

## Install

```bash
# Core: stdlib + pyyaml + dendropy + pytest.
pip install pyyaml dendropy pytest ruff mypy

# Bioinformatics tools (base conda has samtools/mafft/fasttree/raxmlHPC; the rest
# go in a dedicated env so base is untouched):
mamba create -n phylo_extra -c bioconda -c conda-forge iqtree aster -y
export HARNESS_TOOL_PATHS="$HOME/miniconda3/envs/phylo_extra/bin"   # explicit, not magic
# Optional sandbox backend (Linux): bwrap or apptainer.
```

## The one rule

Nothing executes a command except `harness.runner.TaskRunner`, which forces, in
order: **approval gate → tool-registry gate → APPROVED/LEASED/RUNNING → argv-only
execution (optionally sandboxed) → all declared validators → statistical evidence
→ scientific interpretation → terminal state**. A green exit code never becomes a
biological conclusion.

## CLI

```bash
# capture the real environment (4 ENVIRONMENT.* artefacts)
python -m harness capture-env --out runs/env

# validate + checksum a dataset manifest
python -m harness validate-manifest schemas/dataset.manifest.example.yaml

# auditable demo run -> runs/<id>/report.md (13-section §24.14 report)
python -m harness demo-run

# real phylogenomic pipeline: per-gene align (MAFFT) -> trim -> model-select tree
# (IQ-TREE ModelFinder + UFBoot) -> gene-tree discordance -> ASTRAL species tree.
# Sandboxed by default (bwrap/apptainer); --no-sandbox to disable.
python -m harness pipeline GENE1=loci/g1.fasta GENE2=loci/g2.fasta \
    --loci-independent yes --run-id myrun

# resume a crashed/interrupted run (idempotent: re-run the same command)
python -m harness pipeline ... --run-id myrun        # skips completed tasks
python -m harness resume runs/myrun                  # finish unfinished tasks

# reproducibility + comparison
python -m harness aggregate runs/myrun               # -> results.csv (trustworthy column)
python -m harness replay runs/myrun                  # re-run frozen plan, report drift
python -m harness diff runs/runA runs/runB           # config/seed/version/result drift
```

## Reproduce the great-ape example

```bash
examples/fetch_ape_mt_genes.sh          # mitochondrial (LINKED) loci
examples/fetch_ape_nuclear_genes.py     # nuclear (INDEPENDENT) loci -> data/apes_nuclear/

# Mitochondrial: the harness REFUSES the ASTRAL species tree (linked loci ->
# loci_independent FAILED -> LOW_CONFIDENCE), correctly.
python -m harness pipeline COX1=data/apes/COX1.fasta CYTB=data/apes/CYTB.fasta ... \
    --run-id apes

# Nuclear, declared independent: ASTRAL is valid -> the species tree is
# BIOLOGICALLY_INTERPRETABLE, and topological wobble below the support threshold is
# reported as estimation error, NOT ILS.
python -m harness pipeline RAG1=data/apes_nuclear/RAG1.fasta ... \
    --loci-independent yes --run-id apes_nuclear
```

## What it actually does (and refuses to do)

- Refuses to run a tool that is not a registered contract, or that is unavailable.
- Refuses to treat exit code 0 as biological truth.
- Classifies negative results (8 categories) instead of calling them failures.
- Detects degenerate outputs (empty / constant / all-gap) and never reports them clean.
- **Refuses ASTRAL on non-independent loci** (linked / mitochondrial).
- **Counts only well-supported clade conflict as discordance** — not raw RF noise — so it does not overclaim ILS.
- Survives a real `SIGKILL` (rebuilds state from the event log; no zombies); resumes.
- Captures stdout/stderr to files with a byte cap; redacts secrets; per-PID resource metrics.
- Freezes provenance (RUN_MANIFEST: git commit, tool versions, seeds, input checksums) and can replay/diff.

## Dev / CI

```bash
bash scripts/ci.sh        # ruff + mypy + pytest (the exact CI gate, runnable locally)
```

## Honest limitations

- This is **bioinformatics execution infrastructure**, not an LLM-agent harness:
  there is no model/prompt/skills layer (lifecycle hooks exist; see `harness/hooks.py`).
- `SLURMExecutor` is implemented (sbatch/sacct) but **not yet validated on a live cluster**.
- `GPUExecutor` is still a stub.
- The CI workflow passes locally but **GitHub Actions has never been triggered** (no remote).
- Detecting real great-ape ILS needs **genome-scale data**; a handful of short genes
  carries no statistically supported signal (the harness says so).
```
