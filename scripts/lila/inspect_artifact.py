"""Print the provenance and shape of a lila_linkbank artifact.

An artifact is a snapshot of an evolving upstream (the LiLa Lemma Bank + LASLA
gold). Before trusting or shipping one, check what it actually is: its `meta`
provenance, table sizes, gold coverage, and whether it carries length-aware
macron keys. No heavy deps — pure SQLite.

Usage: python inspect_artifact.py <db>
"""

import sqlite3
import sys


def main(db: str) -> None:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    print(f"artifact: {db}")
    print("\nmeta:")
    try:
        for k, v in con.execute("SELECT k, v FROM meta ORDER BY k"):
            print(f"  {k:20} {v}")
    except sqlite3.OperationalError:
        print("  (no meta table — provenance unknown)")

    print("\ntables:")
    for t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:12} {n:>10,} rows")

    if "lemma_uri" in tables:
        cols = {r[1] for r in con.execute("PRAGMA table_info(lemma_uri)")}
        gold = con.execute("SELECT COUNT(*) FROM lemma_uri WHERE is_gold=1").fetchone()[0]
        print(f"\nlemma_uri gold (corpus-attested) rows : {gold:,}")
        print(f"length-aware macron_key column        : "
              f"{'present' if 'macron_key' in cols else 'ABSENT — item-3 payoff unavailable'}")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
