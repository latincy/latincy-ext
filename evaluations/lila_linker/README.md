# lila_linker — evaluation

Reproducible evals for the LiLa Lemma Bank linker. All scripts import the
resolver from the installed `latincy_ext` package (not a build worktree), so a
checkout of latincy-ext + a linkbank artifact is all you need.

## Inputs

- **`<db>`** — a `lila_linkbank*.sqlite` artifact (built per [`../../REBUILD.md`](../../REBUILD.md)).
  Not committed; it is generated data and carries LASLA CC-BY-NC-SA content.
- **gold `*.conllup`** — held-out CoNLL-U-Plus with a LiLa URI column
  (`lilaLemma:` CURIE or a full `.../lemma/` URL).

## Scripts

| Script | Question | Model? |
|---|---|---|
| `coverage_spike.py <db> A <text>` | End-to-end **reach**: do our lemmatizer's outputs land on LiLa keys? | yes |
| `coverage_spike.py <db> B <gold>` | **Table quality**: top-1 vs gold URI (the MFS baseline) | no |
| `analyze_ambiguity.py <db>` | How big/concentrated is the ambiguous tail? | no |
| `rule_eval.py <db> <gold...>` | Does the v2 FEATS/dep rule **beat MFS**? | yes |
| `link_accuracy_sample.py <db> <gold>` | Human-checkable link **correctness** (CSV) | no |

## Read the numbers honestly

- **Coverage ≠ correctness.** Mode A counts a token as covered even when it
  resolved for a *wrong predicted lemma*. It is an upper bound on reach, not an
  accuracy figure.
- **"gold URI" is a claim.** Mode B's top-1 number compares to the treebank's own
  URI column, which is unaudited. `link_accuracy_sample.py` exists precisely so a
  human can adjudicate a sample (`verdict` column) rather than trust that column.
- **One author is one author.** The historical spike used held-out Catullus
  (classical verse). Rerun across genres/eras before quoting a field number.

## Reference results (full artifact, 2026-06)

Reproduced by the graduated scripts:

```
analyze_ambiguity : 27,630 gold (lemma,UPOS) keys; 383 ambiguous (1.4%);
                    19.6% of tokens on an ambiguous key; 80.4% already unique.
coverage_spike B  : Catullus 13,129 gold tokens → 99.0% top-1 == gold (MFS baseline).
```

### `rule_eval` (ut mood) — read the gold before the lift

| corpus | genre / era | gold uses both ut URIs? | MFS | rule | Δ | n |
|---|---|---|---|---|---|---|
| Caesar   | classical prose | yes (LASLA) | 81.3% | 96.3% | **+15.0** | 428 |
| Catullus | classical verse | yes (LASLA) | 60.0% | 82.4% | **+22.4** | 85 |
| Augustine (*Civ. Dei*) | Late Latin prose | **no — all 2,514 ut → 130906** | 100.0% | 85.9% | −14.1 | 2,430 |

The Augustine row is not a rule failure — it is a **gold-blindness artifact**. That
export tags *every* `ut` with the subjunctive/purpose URI (130906) and never uses
the temporal 130905, so MFS is 100% by construction and any distinction the rule
draws is scored wrong. Spot-checking the flips (8 sampled) shows ~6/8 are genuine
comparative/temporal `ut` + indicative the rule correctly identifies (`ut dixi`
"as I said", `ut possum` "as I can", `ut cognouimus` "as we learned"); the ~2/8
real errors trace to **upstream** parser/morph mistakes the rule inherits (a
subjunctive mistagged `Ind`; `ut` attached to a non-verb head), not to the rule
logic.

Takeaways:
- Where the treebank distinguishes the two `ut` senses (LASLA), the rule recovers
  15–22 points at zero training cost — the case for shipping it (opt-in).
- Where the gold lumps senses, blind gold-agreement *understates* the rule. Judge
  it with `link_accuracy_sample.py` (human `verdict`), not the coverage number.
- The rule is only as good as the parse/morph feeding it; that ceiling is real and
  should be quoted alongside the lift.
