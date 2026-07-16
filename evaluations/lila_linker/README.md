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
rule_eval (ut)    : 513 held-out ut tokens → MFS 77.8%, rule 93.96% (+16.2 pts),
                    pick changed on 125 tokens.
```

The `rule_eval` lift is the case for shipping the `ut` mood rule: a hand-written
FEATS/dependency rule recovers ~16 points on the tail it fires on, at zero
training cost — consistent with the notebook's "a cheap rule beats a biencoder".
