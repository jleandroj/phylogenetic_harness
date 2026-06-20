#!/usr/bin/env python3
"""Fetch real NUCLEAR ortholog CDS for great apes from NCBI RefSeq (audit round 4 #7).

Unlike mitochondrial genes (linked = one tree), nuclear loci on different
chromosomes are INDEPENDENT and can show gene-tree discordance from incomplete
lineage sorting — the classic human/chimp/gorilla case. Writes one FASTA per gene
to data/apes_nuclear/.

    python examples/fetch_ape_nuclear_genes.py
    python -m harness pipeline RAG1=data/apes_nuclear/RAG1.fasta ... \
        --loci-independent yes --run-id apes_nuclear

Single-copy nuclear markers used in primate phylogenetics. Best-effort: a gene
that does not return a clean ortholog across all taxa is skipped.
"""
from __future__ import annotations

import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

TAXA = ["Homo sapiens", "Pan troglodytes", "Pan paniscus", "Gorilla gorilla", "Pongo abelii"]
GENES = ["RAG1", "RAG2", "BDNF", "ADORA3", "RHO", "CNR1"]
OUT = Path("data/apes_nuclear")


def _get(url: str, params: dict[str, str]) -> str:
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(full, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                raise
            sys.stderr.write(f"retry ({exc})\n")
            time.sleep(2)
    return ""


def find_accession(gene: str, organism: str) -> str | None:
    term = f'{gene}[gene] AND "{organism}"[orgn] AND refseq[filter] AND biomol_mrna[prop]'
    xml = _get(ESEARCH, {"db": "nuccore", "term": term, "retmax": "1", "sort": "relevance"})
    if "<Id>" in xml:
        return xml.split("<Id>")[1].split("</Id>")[0]
    return None


def longest_cds(accession: str) -> str | None:
    txt = _get(EFETCH, {"db": "nuccore", "id": accession,
                        "rettype": "fasta_cds_na", "retmode": "text"})
    best = ""
    seq: list[str] = []
    for line in txt.splitlines() + [">"]:
        if line.startswith(">"):
            s = "".join(seq)
            if len(s) > len(best):
                best = s
            seq = []
        else:
            seq.append(line.strip())
    return best or None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for gene in GENES:
        taxa_seq: dict[str, str] = {}
        for org in TAXA:
            acc = find_accession(gene, org)
            time.sleep(0.4)
            if not acc:
                sys.stderr.write(f"{gene}/{org}: no accession\n")
                continue
            cds = longest_cds(acc)
            time.sleep(0.4)
            if cds and len(cds) > 200:
                taxa_seq[org.replace(" ", "_")] = cds
        if len(taxa_seq) >= 4:
            (OUT / f"{gene}.fasta").write_text(
                "".join(f">{t}\n{s}\n" for t, s in sorted(taxa_seq.items())), encoding="utf-8")
            print(f"{gene}: {len(taxa_seq)} taxa, lengths {sorted(len(s) for s in taxa_seq.values())}")
        else:
            sys.stderr.write(f"{gene}: only {len(taxa_seq)} taxa -> skipped\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
