# Rebuilding the LiLa linkbank artifact

The `lila_linker` component reads a local SQLite artifact
(`lila_linkbank*.sqlite`). That file is **generated data** — a snapshot of an
evolving upstream — and is *not* committed (like the macronizer lookup JSON).
This document is the provenance + rebuild path so a given artifact can be traced
and regenerated.

## What's in the repo vs. what isn't

| Thing | Where |
|---|---|
| Resolver + normalizers (`normalize_lemma`, `normalize_lemma_macron`) | `src/latincy_ext/lila_linker.py` |
| Disambiguation rules | `src/latincy_ext/lila_disambiguate.py` |
| Eval harness | `evaluations/lila_linker/` |
| Artifact inspector | `scripts/lila/inspect_artifact.py` |
| Macron-key builder | `scripts/lila/build_macron_keys.py` |
| **Heavy backbone build** (MariaDB dump → SQLite, LASLA gold harvest) | `latincy-treebanks` worktree `scripts/lila/` |
| **The `.sqlite` artifact itself** | generated; not committed |

The backbone build stays in latincy-treebanks because it needs the LiLa MariaDB
dump and the LASLA CoNLL-U corpora, which live there. This repo owns the
*runtime* and *eval* surface; graduate the backbone build here only if those
inputs move.

## Pipeline

1. **Backbone** (`build_backbone.py`, treebanks): LiLa `lila_db.sql` →
   `lemma_uri` (complete lemma→URI), `wr_uri` (orthographic variants),
   `form_uri` (attested surface forms). Keys via `normalize_lemma`.
2. **Gold enrichment** (`extract_conllu.py`, treebanks): harvest attested
   `(lemma, UPOS) → URI` from CoNLL-U-Plus corpora; layer on as `is_gold=1` with
   frequencies. This is what makes MFS ranking work — and what carries the LASLA
   CC-BY-NC-SA obligation.
3. **Length-aware keys** (optional, `scripts/lila/build_macron_keys.py`, here):
   add a `macron_key` column so the resolver can split vowel-length homographs
   when `use_macron=True`. Without this step the column is absent and the
   resolver degrades silently to length-blind lookup.

## Normalization parity is load-bearing

Build and runtime **must** produce byte-identical keys or the join silently
misses. Do not re-implement the normalizers in the build — import them:

```python
from latincy_ext.lila_linker import normalize_lemma, normalize_lemma_macron
```

`build_macron_keys.py` already does this; the treebanks backbone build should too
(its local `lila_normalize.py` predates the package and must stay a byte-for-byte
copy — prefer importing the package once latincy-ext is a build dependency).

## Provenance: the `meta` table

`inspect_artifact.py` prints it. Every build should write at least:

| key | meaning |
|---|---|
| `backbone_source` | e.g. `LiLa_Lemma-Bank lila_db.sql` |
| `backbone_license` | `CC-BY-SA 4.0` (backbone); enrichment adds LASLA CC-BY-NC-SA |
| `backbone_version` | dump date / release tag of the LiLa source |
| `built_at` | ISO timestamp of this build |
| `gold_corpora` | which CoNLL-U corpora fed the gold enrichment |
| `schema_version` | bump when table/column layout changes |
| `macron_keys` | `true` once `build_macron_keys.py` has run |

`LilaResolver.meta()` / `LilaLinker.meta_info` surface this at runtime so a
pipeline can log exactly which artifact it linked against.

## Licensing

The **backbone** is CC-BY-SA 4.0. The **gold enrichment** incorporates LASLA data
(CC-BY-NC-SA 4.0) — so any artifact built through step 2 is academic /
non-commercial use only. Keep that obligation visible in `meta.backbone_license`
and anywhere the artifact is distributed.
