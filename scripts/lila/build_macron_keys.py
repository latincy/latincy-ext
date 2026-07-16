"""Add a length-aware ``macron_key`` column to an existing linkbank artifact.

This is the build-side enabler for the resolver's macron path (item 3). The v1
backbone keys are length-blind, so ``mālum``/``malum`` collapse to one key; with
a ``macron_key`` column the resolver can split them when a macronized form is
available at inference (``lila_linker(config={"use_macron": True})``).

Input: a TSV mapping ``lemma<TAB>macronized_lemma`` (e.g. harvested from the
kaikki macron table used by macron_morph). For each `lemma_uri` row whose
``norm_key`` matches ``normalize_lemma(lemma)``, set ``macron_key`` to
``normalize_lemma_macron(macronized_lemma)``. Rows with no macron evidence keep
``macron_key = norm_key`` (so an unmarked query still matches the length-blind
sense). Parity is guaranteed by importing the *package's* normalizers — the same
functions the resolver calls at runtime.

Writes in place after an atomic checkpoint copy. Idempotent.

Usage:
    python build_macron_keys.py <db> <lemma_macron.tsv>
"""

import shutil
import sqlite3
import sys

from latincy_ext.lila_linker import normalize_lemma, normalize_lemma_macron


def _load_map(tsv: str):
    """norm_key -> {macron_key, ...} from a lemma<TAB>macronized_lemma TSV."""
    by_norm: dict[str, set[str]] = {}
    with open(tsv, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            nk = normalize_lemma(parts[0])
            mk = normalize_lemma_macron(parts[1])
            if nk and mk:
                by_norm.setdefault(nk, set()).add(mk)
    return by_norm


def main(db: str, tsv: str) -> None:
    ckpt = db + ".pre-macron.bak"
    shutil.copyfile(db, ckpt)
    print(f"checkpoint: {ckpt}")

    by_norm = _load_map(tsv)
    print(f"macron map: {len(by_norm):,} norm_keys with macronized evidence")

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(lemma_uri)")}
    if "macron_key" not in cols:
        con.execute("ALTER TABLE lemma_uri ADD COLUMN macron_key TEXT")
    # default: length-blind key doubles as macron key (unmarked queries still hit)
    con.execute("UPDATE lemma_uri SET macron_key = norm_key")

    # where we have a *unique* macronized form for a norm_key, use it
    updated = 0
    for nk, mks in by_norm.items():
        if len(mks) == 1:
            con.execute(
                "UPDATE lemma_uri SET macron_key=? WHERE norm_key=?", (next(iter(mks)), nk)
            )
            updated += con.total_changes and 1 or 0
    con.execute("CREATE INDEX IF NOT EXISTS idx_macron_key ON lemma_uri(macron_key, upos)")
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('macron_keys', 'true')")
    con.commit()
    con.close()
    print(f"macron_key column populated (unique-form norm_keys updated: {updated:,})")
    print("NOTE: norm_keys with >1 macronized form are left length-blind — splitting")
    print("      them needs per-URI length evidence, not just the form inventory.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
