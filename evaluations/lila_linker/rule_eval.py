"""Does the v2 FEATS/dependency rule actually beat MFS on the ambiguous tail?

The only number that justifies shipping a disambiguation rule is its accuracy
*relative to the Most-Frequent-Sense baseline* on the tokens it fires on. This
runs the real deployed path — la_core_web_lg parse → lila_linker rule — and
scores it against the gold URIs, restricted to the keys the rule covers
(currently ``ut``/SCONJ).

Alignment: model output and the gold CoNLL-U-Plus come from the *same* text, so
within a sentence the k-th ruled-key occurrence in the parse is matched to the
k-th in the gold. Sentences whose ruled-key counts disagree are skipped (logged)
so a tokenization mismatch can never silently score as a hit or a miss.

Caveat: if MFS scores ~100% here, the corpus's gold almost certainly *lumps* the
key's senses onto one URI (e.g. the Augustine LiLa export tags every ut 130906).
Then any distinction the rule draws is scored wrong and the "lift" goes negative —
a gold-blindness artifact, not rule failure. Adjudicate with link_accuracy_sample.

Usage:
    python rule_eval.py <db> <gold_conllup> [<gold_conllup> ...]
"""

import sys
from collections import Counter

import spacy

import latincy_ext  # noqa: F401 — registers the factory
from latincy_ext.lila_disambiguate import RULES
from latincy_ext.lila_linker import normalize_lemma

from _conllu import sentences, token_uri

RULED = set(RULES)  # {(norm_lemma, upos)}


def _ruled_gold(sent):
    """[(norm_lemma, upos, gold_uri)] for ruled-key tokens in a gold sentence."""
    out = []
    for cols in sent:
        key = (normalize_lemma(cols[2]), cols[3])
        if key in RULED:
            gold_uri, _ = token_uri(cols)
            if gold_uri:
                out.append((key, gold_uri))
    return out


def main() -> None:
    db, files = sys.argv[1], sys.argv[2:]
    nlp = spacy.load("la_core_web_lg")

    # two pipes on the same parse: MFS-only and rule-on
    nlp.add_pipe("lila_linker", name="mfs", config={"db_path": db, "disambiguate": False})
    nlp.add_pipe("lila_linker", name="rule", config={"db_path": db, "disambiguate": True})

    n = mfs_ok = rule_ok = fired = skipped = 0
    per_key = Counter()
    for path in files:
        for sent in sentences(path):
            gold = _ruled_gold(sent)
            if not gold:
                continue
            text = " ".join(c[1] for c in sent)
            doc = nlp(text)
            # ruled-key tokens in the parse, in order
            parsed = [t for t in doc if (normalize_lemma(t.lemma_), t.pos_) in RULED]
            if len(parsed) != len(gold):
                skipped += 1
                continue
            for tok, (key, gold_uri) in zip(parsed, gold):
                n += 1
                # MFS pick == what the token got under the "mfs" run: re-resolve
                mfs_uri = nlp.get_pipe("mfs").resolver.resolve(tok.lemma_, tok.pos_).uri
                rule_uri = tok._.lila_uri  # "rule" pipe ran last → this is the rule pick
                mfs_ok += (mfs_uri == gold_uri)
                rule_ok += (rule_uri == gold_uri)
                if rule_uri != mfs_uri:
                    fired += 1
                    per_key[key] += 1

    print(f"ruled-key gold tokens evaluated : {n:,}")
    print(f"  sentences skipped (misalign)  : {skipped:,}")
    print(f"MFS baseline accuracy           : {100*mfs_ok/max(n,1):.2f}%")
    print(f"rule accuracy                   : {100*rule_ok/max(n,1):.2f}%")
    print(f"  → lift over MFS               : {100*(rule_ok-mfs_ok)/max(n,1):+.2f} pts")
    print(f"rule changed the pick on        : {fired:,} tokens")
    for key, c in per_key.most_common():
        print(f"    {key[0]}/{key[1]}: {c:,}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main()
