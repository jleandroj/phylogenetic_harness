#!/usr/bin/env bash
# Reproducible provenance for the real-data run: fetch 6 mitochondrial CDS genes
# for 5 great apes from NCBI RefSeq and assemble per-gene ortholog FASTAs.
#
#   examples/fetch_ape_mt_genes.sh            # -> data/apes/{COX1,CYTB,ND2,ND4,ATP6,ND5}.fasta
#   python -m harness pipeline COX1=data/apes/COX1.fasta ... --run-id apes
#
# Source: NCBI E-utilities efetch, rettype=fasta_cds_na. RefSeq mitogenomes:
#   Homo sapiens NC_012920.1, Pan troglodytes NC_001643.1, Pan paniscus NC_001644.1,
#   Gorilla gorilla NC_011120.1, Pongo abelii NC_002083.1
set -euo pipefail

EUTILS="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TMP="$(mktemp -d)"
OUT="data/apes"
mkdir -p "$OUT"

declare -A APES=(
  [Homo_sapiens]=NC_012920.1 [Pan_troglodytes]=NC_001643.1 [Pan_paniscus]=NC_001644.1
  [Gorilla_gorilla]=NC_011120.1 [Pongo_abelii]=NC_002083.1
)

for sp in "${!APES[@]}"; do
  echo "fetching $sp (${APES[$sp]}) ..."
  curl -s "${EUTILS}?db=nuccore&id=${APES[$sp]}&rettype=fasta_cds_na&retmode=text" -o "$TMP/$sp.cds.fa"
  sleep 0.4   # be polite to NCBI
done

python3 - "$TMP" "$OUT" <<'PY'
import re, sys
from pathlib import Path
src, out = Path(sys.argv[1]), Path(sys.argv[2])
GENES = ["COX1", "CYTB", "ND2", "ND4", "ATP6", "ND5"]
data = {g: {} for g in GENES}
for f in sorted(src.glob("*.cds.fa")):
    taxon = f.name.replace(".cds.fa", "")
    gene, seq = None, []
    def flush():
        if gene in data and taxon not in data[gene]:
            data[gene][taxon] = "".join(seq)
    for line in f.read_text().splitlines():
        if line.startswith(">"):
            flush()
            m = re.search(r"\[gene=([^\]]+)\]", line)
            gene, seq = (m.group(1) if m else None), []
        else:
            seq.append(line.strip())
    flush()
for g in GENES:
    taxa = data[g]
    if len(taxa) >= 4:
        (out / f"{g}.fasta").write_text("".join(f">{t}\n{s}\n" for t, s in sorted(taxa.items())))
        print(f"{g}: {len(taxa)} taxa")
PY

rm -rf "$TMP"
echo "done -> $OUT/"
