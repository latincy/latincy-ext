"""lila_linker — offline LiLa Lemma Bank linking component for LatinCy.

Resolves every content token's lemma to a LiLa URI using a local SQLite
artifact — no network calls at inference time.

Sets three custom extensions on each non-punct/space token:

- ``token._.lila_uri``        — top-ranked LiLa URI, or None on miss
- ``token._.lila_candidates`` — ranked list of all candidate URIs
- ``token._.lila_source``     — resolution path: ``lemma_pos`` | ``lemma`` |
                                 ``wr`` | ``form`` | ``miss``

Resolution order (v1):
    1. (norm_lemma, UPOS) in lemma_uri  — POS-keyed, handles POS homographs
    2. norm_lemma (any UPOS) in lemma_uri
    3. norm_lemma in wr_uri             — orthographic-variant backbone
    4. norm_form in form_uri            — attestation fallback (candidates only)

Usage::

    import spacy
    import latincy_ext  # registers the factory

    nlp = spacy.load("la_core_web_lg")
    nlp.add_pipe("lila_linker", config={"db_path": "/path/to/lila_linkbank.sqlite"})

    doc = nlp("Gallia est omnis divisa in partes tres.")
    for t in doc:
        print(t.text, t._.lila_uri, t._.lila_source)

The ``db_path`` can also be set via the ``LATINCY_LILA_DB`` environment variable.

Licensing note: the LiLa artifact incorporates LASLA data (CC-BY-NC-SA 4.0).
Academic / non-commercial use only when using the enriched artifact.
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from spacy.language import Language
from spacy.tokens import Doc, Token

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_HOMOGRAPH_RE = re.compile(r"[#_]?\d+$")


def _strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_lemma(s: str) -> str:
    """Canonicalize a lemma to LiLa orthography for exact-match joins.

    Lowercase, v→u, j→i, strip diacritics and homonym markers.
    Must produce identical output at build time and runtime.
    """
    if not s:
        return ""
    s = _strip_diacritics(s)
    s = s.lower()
    s = s.replace("v", "u").replace("j", "i")
    s = _HOMOGRAPH_RE.sub("", s)
    return s.strip()


normalize_form = normalize_lemma


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

@dataclass
class Resolution:
    uri: Optional[str]
    candidates: List[str] = field(default_factory=list)
    kind: Optional[str] = None
    source: str = "miss"


def _rank(rows):
    def key(r):
        uri, kind, freq, is_gold = r
        return (-(is_gold or 0), -(freq or 0), 0 if kind == "lemma" else 1, uri)
    return [r[0] for r in sorted(rows, key=key)]


class LilaResolver:
    """Offline SQLite-backed LiLa lemma → URI resolver."""

    def __init__(self, db_path: str) -> None:
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def _lemma_rows(self, nk: str, upos: str):
        if upos:
            rows = self.con.execute(
                "SELECT uri,kind,freq,is_gold FROM lemma_uri WHERE norm_key=? AND upos=?",
                (nk, upos),
            ).fetchall()
            if rows:
                return rows, True  # (rows, pos_keyed)
        rows = self.con.execute(
            "SELECT uri,kind,freq,is_gold FROM lemma_uri WHERE norm_key=?", (nk,)
        ).fetchall()
        return rows, False

    def _wr_rows(self, nk: str):
        return self.con.execute(
            "SELECT uri,kind,0 AS freq,0 AS is_gold FROM wr_uri WHERE norm_wr=?", (nk,)
        ).fetchall()

    def _form_rows(self, nf: str):
        return self.con.execute(
            "SELECT uri,'?' AS kind,freq,0 AS is_gold FROM form_uri WHERE norm_form=?", (nf,)
        ).fetchall()

    def resolve(
        self,
        lemma: str,
        upos: Optional[str] = None,
        form: Optional[str] = None,
    ) -> Resolution:
        nk = normalize_lemma(lemma)
        upos = upos or ""

        rows, pos_keyed = self._lemma_rows(nk, upos)
        if rows:
            ranked = _rank(rows)
            src = "lemma_pos" if pos_keyed else "lemma"
            return Resolution(ranked[0], ranked, rows[0][1], src)

        rows = self._wr_rows(nk)
        if rows:
            ranked = _rank(rows)
            return Resolution(ranked[0], ranked, rows[0][1], "wr")

        if form:
            rows = self._form_rows(normalize_form(form))
            if rows:
                ranked = _rank(rows)
                return Resolution(None, ranked, None, "form")

        return Resolution(None, [], None, "miss")


# ---------------------------------------------------------------------------
# spaCy component
# ---------------------------------------------------------------------------

_EXTS = ("lila_uri", "lila_candidates", "lila_source")


@Language.factory(
    "lila_linker",
    default_config={"db_path": None},
    assigns=["token._.lila_uri", "token._.lila_candidates", "token._.lila_source"],
)
def make_lila_linker(nlp: Language, name: str, db_path: Optional[str]) -> "LilaLinker":
    resolved = db_path or os.environ.get("LATINCY_LILA_DB")
    if not resolved:
        raise ValueError(
            "lila_linker requires db_path (pass via config or LATINCY_LILA_DB env var)"
        )
    return LilaLinker(resolved)


class LilaLinker:
    """spaCy pipeline component that resolves token lemmas to LiLa URIs."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self.resolver = LilaResolver(self.db_path)
        for ext in _EXTS:
            if not Token.has_extension(ext):
                Token.set_extension(ext, default=None)

    def __call__(self, doc: Doc) -> Doc:
        for t in doc:
            if t.is_punct or t.is_space:
                continue
            res = self.resolver.resolve(t.lemma_, t.pos_, t.text)
            t._.lila_uri = res.uri
            t._.lila_candidates = res.candidates
            t._.lila_source = res.source
        return doc

    def to_disk(self, path, *, exclude=()):
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "cfg.json").write_text(
            f'{{"db_path": "{self.db_path}"}}', encoding="utf-8"
        )

    def from_disk(self, path, *, exclude=()):
        import json
        cfg = json.loads((Path(path) / "cfg.json").read_text(encoding="utf-8"))
        self.db_path = cfg["db_path"]
        self.resolver = LilaResolver(self.db_path)
        return self
