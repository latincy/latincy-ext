"""Coverage / accuracy spike for the LiLa linker — the go/no-go gate.

Two modes, deliberately kept apart because they answer different questions:

  Mode A (pipeline): run la_core_web_lg on held-out RAW text; resolve each
      MODEL-predicted lemma+POS. Measures the end-to-end story — does our
      lemmatizer's output land on LiLa keys? NOTE: coverage != correctness here,
      because a token that resolves for a *wrong* predicted lemma still counts as
      covered. Read it as an upper bound on reach, not accuracy.

  Mode B (oracle): read the held-out work's GOLD lemma+POS+URI straight from its
      CoNLL-U(-Plus); resolve(lemma, upos) and compare the top pick to the gold
      URI. Isolates TABLE quality from lemmatizer/tokenizer noise. The top-1
      number is the Most-Frequent-Sense baseline a disambiguator must beat.
      CAVEAT: "gold URI" is the treebank's own claim, not verified ground truth;
      see link_accuracy_sample.py for a human-checkable view.

Usage:
    python coverage_spike.py <db> A <raw_or_conllu_text_file>
    python coverage_spike.py <db> B <gold_conllup>
"""

import sys
from collections import Counter

from latincy_ext.lila_linker import LilaResolver

from _conllu import text_lines, token_rows, token_uri


def mode_a(db: str, text_file: str) -> None:
    import spacy

    nlp = spacy.load("la_core_web_lg")
    r = LilaResolver(db)
    texts = text_lines(text_file)
    print(f"[A] {len(texts)} sentences from {text_file.split('/')[-1]} → la_core_web_lg")
    src = Counter()
    n = hits = ambig = 0
    for doc in nlp.pipe(texts, batch_size=64):
        for t in doc:
            if t.is_punct or t.is_space:
                continue
            n += 1
            res = r.resolve(t.lemma_, t.pos_, t.text)
            src[res.source] += 1
            if res.uri:
                hits += 1
                if len(res.candidates) > 1:
                    ambig += 1
    print(f"  tokens (non-punct)        : {n:,}")
    print(f"  resolved to a URI         : {hits:,} ({100*hits/max(n,1):.1f}%)   ← coverage")
    print(f"    of those, ambiguous     : {ambig:,} ({100*ambig/max(hits,1):.1f}%)")
    print("  source breakdown          :")
    for s, c in src.most_common():
        print(f"    {s:10} {c:>7,} ({100*c/max(n,1):.1f}%)")


def mode_b(db: str, gold_file: str) -> None:
    r = LilaResolver(db)
    n = cov = top1 = 0
    for cols in token_rows(gold_file):
        gold_uri, _ = token_uri(cols)
        if not gold_uri:
            continue
        n += 1
        res = r.resolve(cols[2], cols[3])
        if res.uri:
            cov += 1
            if res.uri == gold_uri:
                top1 += 1
    print(f"[B] oracle-lemma eval on {gold_file.split('/')[-1]} ({n:,} gold-linked tokens)")
    print(f"  coverage (URI returned)   : {cov:,} ({100*cov/max(n,1):.1f}%)")
    print(f"  top-1 == gold URI         : {top1:,} ({100*top1/max(n,1):.1f}% of all; "
          f"{100*top1/max(cov,1):.1f}% of covered)   ← MFS baseline")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    db, mode, path = sys.argv[1], sys.argv[2], sys.argv[3]
    (mode_a if mode.upper() == "A" else mode_b)(db, path)
