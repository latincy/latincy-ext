<p align="center">
  <img src="https://raw.githubusercontent.com/diyclassics/latincy-ext/main/assets/latincy-ext-logo.jpg" alt="LatinCy Ext" width="400">
</p>

# LatinCy Ext

**Experimental spaCy components for LatinCy pipelines.**

`latincy-ext` provides offline [LiLa Lemma Bank](https://lila-erc.eu/) linking and macron-based morphological disambiguation — no model overrides, no network calls at inference.

## Components

| Component | What it does |
|-----------|-------------|
| `lila_linker` | Resolves every token's lemma to a LiLa Lemma Bank URI via a local SQLite artifact. Four-path resolution (lemma+POS → lemma → orthographic variant → form attestation). |
| `macron_morph` | Macron-based morphological disambiguation. Looks up the macronized form in a kaikki-derived table; sets agreed UD features across all matching parses. |

## Installation

Install from source (PyPI release coming soon):

```bash
git clone https://github.com/diyclassics/latincy-ext
pip install -e latincy-ext
```

## Quick Start — `lila_linker`

```python
import spacy
import latincy_ext  # registers the lila_linker factory

nlp = spacy.load("la_core_web_lg")
nlp.add_pipe("lila_linker", config={"db_path": "/path/to/lila_linkbank.sqlite"})

doc = nlp("Gallia est omnis divisa in partes tres.")
for t in doc:
    print(t.text, t._.lila_uri, t._.lila_source)
# Gallia  http://lila-erc.eu/data/id/lemma/7760   lemma_pos
# est     http://lila-erc.eu/data/id/lemma/126689  lemma_pos
# ...
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

## Quick Start — `macron_morph`

```python
import latincy_ext

nlp = spacy.blank("la")
nlp.add_pipe("macron_morph", config={"lookup_path": "/path/to/latin-forms-macronized-morph.json.gz"})
```

The lookup table is built by `extract_macronized_morph.py` in [latincy-words](https://github.com/diyclassics/latincy-words).

## The SQLite Artifact

The `lila_linkbank.sqlite` (~105 MB) and `lila_linkbank_full.sqlite` (~120 MB) artifacts are **not bundled** with this package — they are too large and carry a distinct license.

**Building the artifact:** see `build_full.sh` in the `lila-lemmabank-linker` branch of [latincy-treebanks](https://github.com/diyclassics/latincy-treebanks).

**License:** The backbone derives from the LiLa Lemma Bank (CC-BY-SA 4.0). The enriched full artifact incorporates LASLA attestation links (CC-BY-NC-SA 4.0); the combined artifact is therefore **CC-BY-NC-SA 4.0**. Academic and non-commercial use is unaffected by the NC clause.

## Demo Notebook

[`notebooks/demo_lila_lemmabank_linker.ipynb`](notebooks/demo_lila_lemmabank_linker.ipynb) walks through the full evaluation: live linking on Caesar, orthographic robustness, coverage spike (**~99.4%** end-to-end on held-out Catullus), oracle eval (MFS baseline), and the v2 disambiguation verdict.

## Acknowledgments

Thank you to Marco Passarotti (Università Cattolica del Sacro Cuore) and the LiLa team for recommending the LatinCy / LiLa Lemma Bank integration.

## Bibliography

The backbone linkbank derives from the **LiLa Lemma Bank** (CIRCSE Research Centre, Università Cattolica del Sacro Cuore, Milan; ERC Consolidator Grant 769994, PI Marco Passarotti); the enriched artifact incorporates attestation links from **LASLA** (Université de Liège). These components extend the **LatinCy** pipelines and the **spaCy** framework; the `macron_morph` lookup is derived from Wiktionary via **Wiktextract** (kaikki.org).

- Burns, P. J. (2023). LatinCy: Synthetic trained pipelines for Latin NLP. *arXiv*. https://doi.org/10.48550/arXiv.2305.04365
- Laboratoire d'Analyse Statistique des Langues Anciennes (LASLA). *Opera Latina* [annotated Latin corpus]. Université de Liège. https://www.lasla.uliege.be/
- Honnibal, M., Montani, I., Van Landeghem, S., & Boyd, A. (2020). spaCy: Industrial-strength natural language processing in Python. *Zenodo*. https://doi.org/10.5281/zenodo.1212303
- Mambrini, F., & Passarotti, M. C. (2023). The LiLa Lemma Bank: A knowledge base of Latin canonical forms. *Journal of Open Humanities Data*, 9(1), 28. https://doi.org/10.5334/johd.145
- Ylönen, T. (2022). Wiktextract: Wiktionary as machine-readable structured data. In *Proceedings of the Thirteenth Language Resources and Evaluation Conference (LREC)* (pp. 1317–1325). https://aclanthology.org/2022.lrec-1.140/
