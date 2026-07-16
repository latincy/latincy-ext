"""Quantify the v2 disambiguation workload from the gold attestations.

Ambiguity is measured on gold rows (is_gold=1) grouped by (norm_key, UPOS):
how many keys map to >1 URI, and — weighted by attestation frequency — what
share of TOKENS land on an ambiguous key. That token share is the slice a
disambiguator must beat MFS on; everything else v1 already resolves uniquely.
The top keys by attestation show where the addressable error concentrates
(and, per the notebook, that it is mostly morphosyntactic, not lexical).

Usage: python analyze_ambiguity.py <db>
"""

import sqlite3
import sys
from collections import Counter

db = sys.argv[1]
con = sqlite3.connect(db)
cur = con.cursor()

rows = cur.execute(
    """
    SELECT norm_key, upos, COUNT(DISTINCT uri) AS ncand, SUM(freq) AS tok
    FROM lemma_uri WHERE is_gold=1 AND upos<>''
    GROUP BY norm_key, upos
    """
).fetchall()

keys = len(rows)
amb_keys = sum(1 for r in rows if r[2] > 1)
tok_total = sum((r[3] or 0) for r in rows)
tok_amb = sum((r[3] or 0) for r in rows if r[2] > 1)

print(f"gold (lemma,UPOS) keys    : {keys:,}")
print(f"  ambiguous (>1 URI)      : {amb_keys:,} ({100*amb_keys/max(keys,1):.1f}% of keys)")
print(f"gold attested tokens      : {tok_total:,}")
print(f"  on an ambiguous key     : {tok_amb:,} ({100*tok_amb/max(tok_total,1):.1f}% of tokens)  ← workload")
print(f"  v1 already unique       : {100*(tok_total-tok_amb)/max(tok_total,1):.1f}% of tokens")

print("\ncandidate-count distribution (keys):")
dist = Counter(min(r[2], 5) for r in rows)
for nc in sorted(dist):
    print(f"  {'5+' if nc == 5 else nc} URIs: {dist[nc]:,}")

print("\ntop ambiguous keys by attestation (where the addressable error concentrates):")
for nk, upos, nc, tok in sorted((r for r in rows if r[2] > 1), key=lambda r: -(r[3] or 0))[:15]:
    uris = cur.execute(
        "SELECT uri,freq FROM lemma_uri WHERE norm_key=? AND upos=? AND is_gold=1 ORDER BY freq DESC",
        (nk, upos),
    ).fetchall()
    ids = ", ".join(f"{u.rsplit('/', 1)[1]}({f})" for u, f in uris)
    print(f"  {nk:12} {upos:5} {nc} URIs, {tok:>6,} tok: {ids}")
con.close()
