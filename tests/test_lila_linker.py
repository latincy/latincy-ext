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
URI_FORM  = "http://lila-erc.eu/data/id/lemma/444"  # reached via form_uri only

# real LiLa URIs the ut rule keys on
UT_PURPOSE  = "http://lila-erc.eu/data/id/lemma/130906"  # +subjunctive (MFS)
UT_TEMPORAL = "http://lila-erc.eu/data/id/lemma/130905"  # +indicative


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
            ("sum",     "AUX",  URI_SUM,     "lemma", 100, 1),
            ("sum",     "VERB", URI_SUM,     "lemma",  10, 0),
            ("ciuitas", "NOUN", URI_CIUIS,   "lemma",  50, 1),
            # a genuinely ambiguous (lemma,UPOS): 78% / 22% split → tests
            # confidence/margin and the ut disambiguation rule.
            ("ut",      "SCONJ", UT_PURPOSE,  "lemma", 13927, 1),
            ("ut",      "SCONJ", UT_TEMPORAL, "lemma",  3902, 1),
        ],
    )
    con.execute("INSERT INTO meta VALUES ('backbone_license', 'CC-BY-SA 4.0')")
    con.executemany(
        "INSERT INTO wr_uri VALUES (?,?,?)",
        [
            ("ciuitatem", URI_WR, "lemma"),  # single orthographic variant
            # two backbone candidates, no attestation → confidence is unknowable
            ("ambiwr", URI_SUM, "lemma"),
            ("ambiwr", URI_CIUIS, "lemma"),
        ],
    )
    con.executemany(
        "INSERT INTO form_uri VALUES (?,?,?)",
        [("formfall", URI_FORM, 7)],  # attested only as a surface form
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

    def test_bare_lemma_fallback_on_pos_miss(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        # "sum" exists under AUX/VERB but not ADV — POS-keyed lookup misses,
        # so it falls back to the bare-lemma (any-UPOS) path.
        res = LilaResolver(db_path).resolve("sum", "ADV")
        assert res.uri == URI_SUM
        assert res.source == "lemma"

    def test_form_fallback_is_candidates_only(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        # lemma misses lemma_uri and wr_uri; surface form hits form_uri.
        # By design the form path returns candidates but no top URI.
        res = LilaResolver(db_path).resolve("zzznolemma", "NOUN", "formfall")
        assert res.source == "form"
        assert res.uri is None
        assert res.candidates == [URI_FORM]

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

    def test_env_var_fallback(self, db_path, monkeypatch):
        # With no db_path in config, the factory falls back to LATINCY_LILA_DB.
        monkeypatch.setenv("LATINCY_LILA_DB", db_path)
        _nlp = spacy.blank("la")
        _nlp.add_pipe("lila_linker", config={"db_path": None})
        doc = _doc_with_lemma("sum", "AUX")
        doc = _nlp.get_pipe("lila_linker")(doc)
        assert doc[0]._.lila_uri == URI_SUM


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_disk_from_disk_roundtrip(self, db_path, tmp_path):
        from latincy_ext.lila_linker import LilaLinker
        save_dir = tmp_path / "pipe"
        LilaLinker(db_path, disambiguate=True, form_policy="promote").to_disk(str(save_dir))

        restored = LilaLinker(db_path).from_disk(str(save_dir))
        assert restored.db_path == db_path
        assert restored.disambiguate is True
        assert restored.form_policy == "promote"
        # resolver still works after reload
        res = restored.resolver.resolve("sum", "AUX")
        assert res.uri == URI_SUM


# ---------------------------------------------------------------------------
# Item 2 — confidence / margin
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_single_candidate_is_fully_confident(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("ciuitas", "NOUN")
        assert res.confidence == 1.0
        assert res.margin == 1.0

    def test_ambiguous_key_confidence_is_top_share(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("ut", "SCONJ")
        # 13927 / (13927+3902) == 0.781
        assert res.confidence == pytest.approx(13927 / 17829, abs=1e-6)
        assert res.margin == pytest.approx((13927 - 3902) / 17829, abs=1e-6)

    def test_single_backbone_hit_is_confident(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        # one backbone candidate → nothing to compete with → confident link
        res = LilaResolver(db_path).resolve("ciuitatem", "NOUN")
        assert res.source == "wr"
        assert res.confidence == 1.0

    def test_multi_backbone_hit_has_no_confidence(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        # ≥2 backbone candidates with no attestation → confidence is unknowable,
        # not fabricated from a zero-frequency tie
        res = LilaResolver(db_path).resolve("ambiwr", "NOUN")
        assert res.source == "wr"
        assert len(res.candidates) == 2
        assert res.confidence is None
        assert res.margin is None

    def test_component_sets_confidence_extension(self, linker):
        doc = _doc_with_lemma("ut", "SCONJ")
        doc = linker(doc)
        assert doc[0]._.lila_confidence == pytest.approx(13927 / 17829, abs=1e-6)


# ---------------------------------------------------------------------------
# Item 5 — form_uri policy
# ---------------------------------------------------------------------------

class TestFormPolicy:
    def test_abstain_is_default(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path).resolve("zzznolemma", "NOUN", "formfall")
        assert res.source == "form"
        assert res.uri is None
        assert res.candidates == [URI_FORM]

    def test_promote_returns_top_uri(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(db_path, form_policy="promote").resolve(
            "zzznolemma", "NOUN", "formfall"
        )
        assert res.source == "form"
        assert res.uri == URI_FORM

    def test_invalid_policy_rejected(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        with pytest.raises(ValueError, match="form_policy"):
            LilaResolver(db_path, form_policy="nonsense")


# ---------------------------------------------------------------------------
# Item 1 — FEATS/dep disambiguation rule (ut by clause mood)
# ---------------------------------------------------------------------------

def _ut_doc(mood: str) -> "spacy.tokens.Doc":
    """Doc 'ut <verb>' with ut(SCONJ) marking a governed verb of the given mood."""
    nlp = spacy.blank("la")
    doc = nlp.make_doc("ut uenit")
    doc[0].lemma_, doc[0].pos_ = "ut", "SCONJ"
    doc[1].lemma_, doc[1].pos_ = "uenio", "VERB"
    doc[1].set_morph(f"Mood={mood}|VerbForm=Fin")
    doc[0].head = doc[1]  # ut attaches as mark to its clause verb
    return doc


class TestDisambiguation:
    def test_indicative_ut_picks_temporal(self, db_path):
        _nlp = spacy.blank("la")
        _nlp.add_pipe("lila_linker", config={"db_path": db_path, "disambiguate": True})
        pipe = _nlp.get_pipe("lila_linker")
        doc = pipe(_ut_doc("Ind"))
        assert doc[0]._.lila_uri == UT_TEMPORAL
        assert doc[0]._.lila_source == "rule:ut_mood"
        assert doc[0]._.lila_candidates[0] == UT_TEMPORAL  # pick surfaced first

    def test_subjunctive_ut_keeps_mfs(self, db_path):
        _nlp = spacy.blank("la")
        _nlp.add_pipe("lila_linker", config={"db_path": db_path, "disambiguate": True})
        pipe = _nlp.get_pipe("lila_linker")
        doc = pipe(_ut_doc("Sub"))
        assert doc[0]._.lila_uri == UT_PURPOSE
        assert doc[0]._.lila_source == "lemma_pos"

    def test_disambiguate_off_leaves_mfs(self, db_path):
        _nlp = spacy.blank("la")
        _nlp.add_pipe("lila_linker", config={"db_path": db_path, "disambiguate": False})
        pipe = _nlp.get_pipe("lila_linker")
        doc = pipe(_ut_doc("Ind"))
        assert doc[0]._.lila_uri == UT_PURPOSE  # MFS, rule not consulted

    def test_governed_mood_reads_head(self):
        from latincy_ext.lila_disambiguate import governed_mood
        assert governed_mood(_ut_doc("Ind")[0]) == "Ind"
        assert governed_mood(_ut_doc("Sub")[0]) == "Sub"


# ---------------------------------------------------------------------------
# Item 3 — macron length-aware key
# ---------------------------------------------------------------------------

URI_MALUM_APPLE = "http://lila-erc.eu/data/id/lemma/555"  # mālum
URI_MALUM_EVIL  = "http://lila-erc.eu/data/id/lemma/556"  # malum


@pytest.fixture(scope="module")
def macron_db_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("lila_macron") / "linkbank.sqlite"
    con = sqlite3.connect(str(p))
    con.executescript("""
        CREATE TABLE lemma_uri (
            norm_key   TEXT NOT NULL,
            macron_key TEXT,
            upos       TEXT NOT NULL,
            uri        TEXT NOT NULL,
            kind       TEXT NOT NULL,
            freq       INTEGER NOT NULL DEFAULT 0,
            is_gold    INTEGER NOT NULL DEFAULT 0
        );
    """)
    con.executemany(
        "INSERT INTO lemma_uri(norm_key,macron_key,upos,uri,kind,freq,is_gold) VALUES (?,?,?,?,?,?,?)",
        [
            ("malum", "mālum", "NOUN", URI_MALUM_APPLE, "lemma", 30, 1),
            ("malum", "malum", "NOUN", URI_MALUM_EVIL,  "lemma", 70, 1),
        ],
    )
    con.commit()
    con.close()
    return str(p)


class TestMacronKey:
    def test_normalize_macron_preserves_length(self):
        from latincy_ext.lila_linker import normalize_lemma_macron, normalize_lemma
        assert normalize_lemma_macron("mālum") != normalize_lemma_macron("malum")
        # length-blind key still collapses them
        assert normalize_lemma("mālum") == normalize_lemma("malum")

    def test_macron_capability_detected(self, macron_db_path, db_path):
        from latincy_ext.lila_linker import LilaResolver
        assert LilaResolver(macron_db_path).has_macron is True
        assert LilaResolver(db_path).has_macron is False

    def test_macron_splits_length_homographs(self, macron_db_path):
        from latincy_ext.lila_linker import LilaResolver
        r = LilaResolver(macron_db_path)
        assert r.resolve("malum", "NOUN", macron="mālum").uri == URI_MALUM_APPLE
        assert r.resolve("malum", "NOUN", macron="malum").uri == URI_MALUM_EVIL

    def test_macron_source_tag(self, macron_db_path):
        from latincy_ext.lila_linker import LilaResolver
        res = LilaResolver(macron_db_path).resolve("malum", "NOUN", macron="mālum")
        assert res.source == "macron_pos"

    def test_no_macron_falls_back_to_mfs(self, macron_db_path):
        from latincy_ext.lila_linker import LilaResolver
        # without a macron signal, length-blind key → MFS by freq (evil, 70)
        assert LilaResolver(macron_db_path).resolve("malum", "NOUN").uri == URI_MALUM_EVIL

    def test_macron_ignored_when_artifact_lacks_keys(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        # passing a macron signal to a non-macron artifact must not error
        res = LilaResolver(db_path).resolve("sum", "AUX", macron="sūm")
        assert res.uri == URI_SUM


# ---------------------------------------------------------------------------
# Item 6 — provenance / meta
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_resolver_reads_meta(self, db_path):
        from latincy_ext.lila_linker import LilaResolver
        assert LilaResolver(db_path).meta().get("backbone_license") == "CC-BY-SA 4.0"

    def test_component_exposes_meta(self, linker):
        assert linker.meta_info.get("backbone_license") == "CC-BY-SA 4.0"
