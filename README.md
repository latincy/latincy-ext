# latincy-ext

Experimental spaCy components for LatinCy pipelines. Offline LiLa Lemma Bank linking and macron-based morphological disambiguation — no model overrides.

## Components

| Component | What it does |
|-----------|-------------|
| `lila_linker` | Resolves every token's lemma to a [LiLa Lemma Bank](https://lila-erc.eu/) URI via a local SQLite artifact. No network calls at inference. |
| `macron_morph` | Macron-based morphological disambiguation. Looks up the macronized form in a kaikki-derived table; sets agreed UD features across all matching parses. |

## Installation

```bash
# editable / dev
git clone https://github.com/diyclassics/latincy-ext
pip install -e latincy-ext   # or: uv pip install -e latincy-ext
```

Once on PyPI: `pip install latincy-ext`

## Quick start — `lila_linker`

```python
import spacy
import latincy_ext  # registers the lila_linker factory

nlp = spacy.load("la_core_web_lg")
nlp.add_pipe("lila_linker", config={"db_path": "/path/to/lila_linkbank.sqlite"})

doc = nlp("Gallia est omnis divisa in partes tres.")
for t in doc:
    print(t.text, t._.lila_uri, t._.lila_source)
```

Each non-punct token gets three attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `token._.lila_uri` | `str \| None` | Top-ranked LiLa URI, or `None` on miss |
| `token._.lila_candidates` | `list[str]` | Ranked candidate URIs (for future disambiguation) |
| `token._.lila_source` | `str` | Resolution path: `lemma_pos` · `lemma` · `wr` · `form` · `miss` |

**Producer-side use** (no spaCy pipeline needed):

```python
from latincy_ext.lila_linker import LilaResolver
r = LilaResolver("/path/to/lila_linkbank.sqlite")
res = r.resolve("divido", "VERB")
print(res.uri, res.source)   # http://lila-erc.eu/data/id/lemma/…  lemma_pos
```

The `db_path` can also be set via the `LATINCY_LILA_DB` environment variable.

## The SQLite artifact

The `lila_linkbank.sqlite` (~101 MB) and `lila_linkbank_full.sqlite` (~115 MB) artifacts are **not bundled** with this package — they are too large and carry a distinct license.

**Building the artifact:** see `build_full.sh` in the `lila-lemmabank-linker` branch of [latincy-treebanks](https://github.com/diyclassics/latincy-treebanks).

**License:** The backbone derives from the LiLa Lemma Bank (CC-BY-SA 4.0). The enriched full artifact incorporates LASLA attestation links (CC-BY-NC-SA 4.0); the combined artifact is therefore **CC-BY-NC-SA 4.0**. Academic and non-commercial use is unaffected by the NC clause. A backbone-only build (CC-BY-SA 4.0, no LASLA enrichment) is under consideration.

## Quick start — `macron_morph`

```python
import latincy_ext

nlp = spacy.blank("la")
nlp.add_pipe("macron_morph", config={"lookup_path": "/path/to/latin-forms-macronized-morph.json.gz"})
```

The lookup table is built by `extract_macronized_morph.py` in [latincy-words](https://github.com/diyclassics/latincy-words).

## Demo notebook

`notebooks/lila_lemmabank_linker_demo.ipynb` walks through the full evaluation: live linking on Caesar, orthographic robustness, coverage spike (**~99.3%** end-to-end on held-out Catullus), oracle eval (MFS baseline), and the v2 disambiguation verdict.

## Acknowledgments

- **LiLa Lemma Bank** — CIRCSE Research Centre, Università Cattolica del Sacro Cuore, Milan. ERC Consolidator Grant 769994 (PI: Marco Passarotti).
- **LASLA** — Laboratoire d'Analyse Statistique des Langues Anciennes, Université de Liège.
