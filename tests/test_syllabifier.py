"""Tests for the syllabifier spaCy component (latincy_ext.syllabifier)."""

import pytest
import spacy
from spacy.tokens import Token

import latincy_ext  # noqa: F401 — registers the "syllabifier" factory
from latincy_ext.syllabifier import SyllabifierComponent


@pytest.fixture
def nlp():
    _nlp = spacy.blank("la")
    _nlp.add_pipe("syllabifier")
    return _nlp


def test_factory_registered(nlp):
    assert "syllabifier" in nlp.pipe_names


def test_bare_path_honest_x(nlp):
    doc = nlp("rosa")
    assert doc[0]._.syllables == ["ro", "sa"]
    assert doc[0]._.qshape == "x.x"          # no macron -> honest x


def test_bare_positional_heavy(nlp):
    doc = nlp("arma")
    assert doc[0]._.qshape == "H.x"          # ar closed, a open unknown


def test_punct_and_space_skipped(nlp):
    doc = nlp("rosa .")
    punct = [t for t in doc if t.is_punct][0]
    assert punct._.syllables is None
    assert punct._.qshape == ""


def test_reads_macronizer_output():
    # simulate the macronizer having set token._.macronized in context
    if not Token.has_extension("macronized"):
        Token.set_extension("macronized", default=None)
    nlp = spacy.blank("la")
    nlp.add_pipe("syllabifier")
    doc = nlp.make_doc("amicus")
    doc[0]._.macronized = "amīcus"
    nlp.get_pipe("syllabifier")(doc)
    assert doc[0]._.syllables == ["a", "mī", "cus"]
    assert doc[0]._.qshape == "L.H.H"        # natura resolved via macrons


def test_macronizer_resolves_syncretism():
    if not Token.has_extension("macronized"):
        Token.set_extension("macronized", default=None)
    nlp = spacy.blank("la")
    nlp.add_pipe("syllabifier")
    doc = nlp.make_doc("rosa rosa")
    doc[0]._.macronized = "rosa"             # nominative (short)
    doc[1]._.macronized = "rosā"             # ablative (long)
    nlp.get_pipe("syllabifier")(doc)
    assert doc[0]._.qshape == "x.x"          # unmarked -> honest
    assert doc[1]._.qshape == "L.H"          # ablative resolved


def test_use_macronizer_output_false_ignores_extension():
    if not Token.has_extension("macronized"):
        Token.set_extension("macronized", default=None)
    nlp = spacy.blank("la")
    nlp.add_pipe("syllabifier", config={"use_macronizer_output": False})
    doc = nlp.make_doc("amicus")
    doc[0]._.macronized = "amīcus"
    nlp.get_pipe("syllabifier")(doc)
    assert doc[0]._.qshape == "x.x.H"        # ignores macronized; i stays honest x


def test_bytes_roundtrip():
    nlp = spacy.blank("la")
    nlp.add_pipe("syllabifier", config={"use_macronizer_output": False})
    data = nlp.get_pipe("syllabifier").to_bytes()
    fresh = SyllabifierComponent(nlp, "syllabifier")
    fresh.from_bytes(data)
    assert fresh.use_macronizer_output is False


def test_pipeline_disk_roundtrip(tmp_path):
    nlp = spacy.blank("la")
    nlp.add_pipe("syllabifier")
    nlp.to_disk(tmp_path / "model")
    nlp2 = spacy.load(tmp_path / "model")
    doc = nlp2("amo")
    assert doc[0]._.qshape == "x.x"
