"""Emit a human-checkable sample of links — because "top-1 == gold URI" trusts
the treebank's own URI column, which is a *claim*, not verified ground truth.

Draws a deterministic sample of gold-linked tokens (optionally only ambiguous or
only disagreements), resolves each, and writes a CSV a human can adjudicate:

    form, lemma, upos, source, confidence, gold_uri, pred_uri, agree, n_cand

Sort by confidence ascending to review the shakiest links first; fill in a
``verdict`` column by hand. This is the eval the headline coverage number cannot
give you — real link *correctness*, not agreement with an unaudited column.

Deterministic sampling: every k-th eligible token (stride = ceil(total/n)); no
RNG, so a rerun on the same input reproduces the same rows.

Usage:
    python link_accuracy_sample.py <db> <gold_conllup> [--n 100] [--only disagree|ambiguous|all] [--out sample.csv]
"""

import argparse
import csv
import math
import sys

from latincy_ext.lila_linker import LilaResolver

from _conllu import token_rows, token_uri


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("gold")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--only", choices=["all", "ambiguous", "disagree"], default="all")
    ap.add_argument("--out", default="link_sample.csv")
    args = ap.parse_args()

    r = LilaResolver(args.db)

    eligible = []
    for cols in token_rows(args.gold):
        gold_uri, _ = token_uri(cols)
        if not gold_uri:
            continue
        res = r.resolve(cols[2], cols[3])
        agree = res.uri == gold_uri
        n_cand = len(res.candidates)
        if args.only == "ambiguous" and n_cand < 2:
            continue
        if args.only == "disagree" and agree:
            continue
        eligible.append((cols[1], cols[2], cols[3], res.source,
                         res.confidence, gold_uri, res.uri, agree, n_cand))

    if not eligible:
        sys.exit("no eligible tokens for the chosen filter")

    stride = max(1, math.ceil(len(eligible) / args.n))
    sample = eligible[::stride][: args.n]
    # shakiest first: unknown confidence, then lowest
    sample.sort(key=lambda row: (row[4] is not None, row[4] if row[4] is not None else 0.0))

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["form", "lemma", "upos", "source", "confidence",
                    "gold_uri", "pred_uri", "agree", "n_cand", "verdict"])
        for row in sample:
            conf = "" if row[4] is None else f"{row[4]:.3f}"
            w.writerow([row[0], row[1], row[2], row[3], conf,
                        row[5], row[6], row[7], row[8], ""])

    agree = sum(1 for row in sample if row[7])
    print(f"eligible tokens     : {len(eligible):,}  (filter: {args.only})")
    print(f"sampled             : {len(sample):,}  (stride {stride})")
    print(f"agree w/ gold column: {agree}/{len(sample)} "
          f"({100*agree/len(sample):.1f}%)  ← NOT accuracy; adjudicate 'verdict' by hand")
    print(f"wrote               : {args.out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main()
