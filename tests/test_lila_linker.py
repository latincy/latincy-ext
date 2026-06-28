"""Tests for the LiLaLinkerComponent and its resolver/normalizer.

Uses a tiny in-memory SQLite fixture — no external artifact required.
Schema mirrors the real linkbank (lemma_uri, wr_uri, form_uri, meta).
"""

import sqlite3
import pytest
import spacy

import latincy_ext  # noqa: F401 — registers factories


URI_SUM   = "http://lila-erc.eu/data/id/lemma/111"
URI_CIUIS = "http://lila-erc.eu/data/id/lemma/222"
URI_WR    = "http://lila-erc.eu/data/id/lemma/333"  # reached via wr_uri only


def _build_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE lemma_uri (
            norm_key TEXT NOT NULL,
            upos     TEXT NOT NULL,
            uri      TEXT NOT NULL,
            kind     TEXT NOT NULL,
            freq     INTEGER NOT NULL DEFAULT 0,
            is_gold  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (norm_key, upos, uri)
        );
        CREATE TABLE wr_uri (
            norm_wr TEXT NOT NULL,
            uri     TEXT NOT NULL,
            kind    TEXT NOT NULL,
            PRIMARY KEY (norm_wr, uri)
        );
        CREATE TABLE form_uri (
            norm_form TEXT NOT NULL,
            uri       TEXT NOT NULL,
            freq      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (norm_form, uri)
        );
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    con.executemany(
        "INSERT INTO lemma_uri VALUES (?,?,?,?,?,?)",
        [
            ("sum",     "AUX",  URI_SUM,   "lemma", 100, 1),
            ("sum",     "VERB", URI_SUM,   "lemma",  10, 0),
            ("ciuitas", "NOUN", URI_CIUIS, "lemma",  50, 1),
        ],
    )
    con.executemany(
        "INSERT INTO wr_uri VALUES (?,?,?)",
        [("ciuitatem", URI_WR, "lemma")],  # an orthographic variant
    )
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("lila") / "linkbank.sqlite"
    _build_db(str(p))
    return str(p)


# ---------------------------------------------------------------------------
# normalize_lemma
# ---------------------------------------------------------------------------

class TestNormalizeLemma:
    def test_v_to_u(self):
        from latincy_ext.lila_linker import normalize_lemma
        assert normalize_lemma("civitas") == "ciuitas"

    def test_j_to_i(self):
        from latincy_ext.lila_linker import normalize_lemma
        assert normalize_lemma("Juppiter") == "iuppiter"

    def test_uppercase_lowercased(self):
        from latincy_ext.lila_linker import normalize_lemma
        assert normalize_lemma("VOLO") == "uolo"

    def test_diacritics_stripped(self):
        from latincy_ext.lila_linker import normalize_lemma
        assert normalize_lemma("mālum") == "malum"

    def test_homonym_marker_stripped(self):
        from latincy_ext.lila_linker import normalize_lemma
        assert normalize_lemma("sum#1") == "sum"

    def test_idempotent(self):
        from latincy_ext.lila_linker import normalize_lemma
        for w in ("Civitas", "uolo", "Jam", "MALVS"):
            once = normalize_lemma(w)
            assert normalize_lemma(once) == once


# ---------------------------------------------------------------------------
# LilaResolver
# ---------------------------------------------------------------------------

class TestLilaResolver:
    def test_lemma_pos_hit_returns_uri(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("sum", "AUX")
        assert res.uri == URI_SUM

    def test_lemma_pos_hit_source_is_lemma_pos(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("sum", "AUX")
        assert res.source == "lemma_pos"

    def test_orthographic_normalization(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        r = LilaResolver(db_path)
        assert r.resolve("civitas", "NOUN").uri == r.resolve("ciuitas", "NOUN").uri

    def test_wr_fallback(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        # "ciuitatem" not in lemma_uri but is in wr_uri
        res = LilaResolver(db_path).resolve("ciuitatem", "NOUN")
        assert res.uri == URI_WR
        assert res.source == "wr"

    def test_miss_returns_empty_resolution(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("zzznothing", "NOUN")
        assert res.uri is None
        assert res.candidates == []
        assert res.source == "miss"

    def test_candidates_list_populated_on_hit(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("sum", "AUX")
        assert URI_SUM in res.candidates


# ---------------------------------------------------------------------------
# LilaLinker spaCy component
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def linker(db_path):
    _nlp = spacy.blank("la")
    _nlp.add_pipe("lila_linker", config={"db_path": db_path})
    return _nlp.get_pipe("lila_linker")


def _doc_with_lemma(lemma: str, pos: str = "NOUN") -> "spacy.tokens.Doc":
    """Make a single-token doc with lemma_ and pos_ set explicitly."""
    _nlp = spacy.blank("la")
    doc = _nlp.make_doc(lemma)
    doc[0].lemma_ = lemma
    doc[0].pos_ = pos
    return doc


class TestLilaLinkerComponent:
    def test_token_gets_lila_uri(self, linker):
        doc = _doc_with_lemma("sum", "AUX")
        doc = linker(doc)
        assert doc[0]._.lila_uri == URI_SUM

    def test_token_gets_lila_source(self, linker):
        doc = _doc_with_lemma("sum", "AUX")
        doc = linker(doc)
        assert doc[0]._.lila_source is not None

    def test_token_gets_candidates_list(self, linker):
        doc = _doc_with_lemma("sum", "AUX")
        doc = linker(doc)
        assert isinstance(doc[0]._.lila_candidates, list)
        assert len(doc[0]._.lila_candidates) >= 1

    def test_missing_lemma_returns_none_uri(self, linker):
        doc = _doc_with_lemma("xyzzy", "NOUN")
        doc = linker(doc)
        assert doc[0]._.lila_uri is None

    def test_no_db_path_raises(self):
        _nlp = spacy.blank("la")
        with pytest.raises(ValueError, match="db_path"):
            _nlp.add_pipe("lila_linker", config={"db_path": None})
            _nlp("test")
