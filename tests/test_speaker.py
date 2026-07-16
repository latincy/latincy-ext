"""Tests for the speaker TTS component.

Pure logic (IPA→Kirshenbaum, input assembly, IPA attachment, serialization) is
covered without any TTS engine; the subprocess call is mocked so no audio is
produced and espeak-ng need not be installed.
"""

import json
from pathlib import Path
from unittest import mock

import pytest
import spacy
from spacy.tokens import Token

import latincy_ext  # noqa: F401  registers the "speaker" factory
from latincy_ext.speaker import SpeakerComponent, ipa_to_kirshenbaum


# --- pure IPA → Kirshenbaum mapping ---------------------------------------

def test_ipa_to_kirshenbaum_basic():
    # amīcus [aˈmiː.kʊs]: stress precedes the 2nd syllable, ː→:, dot dropped
    assert ipa_to_kirshenbaum("aˈmiː.kʊs") == "a'mi:kUs"


def test_ipa_to_kirshenbaum_strips_brackets_and_drops_glide():
    # thēsaurus: brackets stripped, diphthong glide (◌̯) dropped, ː kept
    assert ipa_to_kirshenbaum("[tʰeːˈsau̯.rʊs]") == "te:'saurUs"


def test_ipa_to_kirshenbaum_unknown_dropped():
    assert ipa_to_kirshenbaum("ɔ̃x") == "O"  # nasal tilde dropped, x unknown dropped


# --- factory + IPA attachment ---------------------------------------------

@pytest.fixture
def lookup_file(tmp_path):
    data = {
        "arma":  [{"natura": "S.S", "syllables": ["ar", "ma"], "macronized": "arma", "ipa": "ˈar.ma"}],
        "cano":  [{"natura": "S.L", "syllables": ["ca", "noː"], "macronized": "canō", "ipa": "ˈka.noː"},
                  {"natura": "L.L", "syllables": ["caː", "noː"], "macronized": "cānō", "ipa": "ˈkaː.noː"}],
    }
    p = tmp_path / "ipa.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


@pytest.fixture
def nlp(lookup_file):
    n = spacy.blank("la")
    # qshape is read to disambiguate readings; register it if the syllabifier hasn't.
    if not Token.has_extension("qshape"):
        Token.set_extension("qshape", default="")
    n.add_pipe("speaker", config={"lookup_path": lookup_file})
    return n


def test_factory_registered_and_attaches_ipa(nlp):
    doc = nlp("arma cano")
    assert doc[0]._.ipa == "ˈar.ma"
    # cano has two readings; with no qshape set, first reading wins.
    assert doc[1]._.ipa == "ˈka.noː"


def test_qshape_disambiguates_reading(nlp):
    doc = nlp("cano")
    doc[0]._.qshape = "L.L"          # 2 syllables — both readings have 2, first still matches
    # force a mismatch-free selection path
    spk = nlp.get_pipe("speaker")
    assert spk._lookup_ipa(doc[0]) == "ˈka.noː"


def test_unknown_token_gets_no_ipa(nlp):
    doc = nlp("xyzzy")
    assert doc[0]._.ipa is None


# --- espeak input assembly + dry run --------------------------------------

def test_espeak_input_phoneme_blocks_and_fallback(nlp):
    doc = nlp("arma xyzzy")            # arma has IPA, xyzzy does not
    spk = nlp.get_pipe("speaker")
    text = spk.espeak_input(doc)
    assert text == f"[[{ipa_to_kirshenbaum('ˈar.ma')}]] xyzzy"


def test_dry_run_returns_assembled_input(nlp):
    doc = nlp("arma")
    out = nlp.get_pipe("speaker").speak(doc, dry_run=True)
    assert out == f"[[{ipa_to_kirshenbaum('ˈar.ma')}]]"


def test_use_ipa_false_uses_plain_text(nlp):
    doc = nlp("arma")
    out = nlp.get_pipe("speaker").speak(doc, dry_run=True, use_ipa=False)
    assert out == "arma"


# --- synthesis dispatch (mocked; no engine required) ----------------------

def test_speak_invokes_espeak(nlp, tmp_path):
    doc = nlp("arma cano")
    spk = nlp.get_pipe("speaker")
    wav = str(tmp_path / "out.wav")
    with mock.patch("latincy_ext.speaker.shutil.which", return_value="/usr/bin/espeak-ng"), \
         mock.patch("latincy_ext.speaker.subprocess.run") as run:
        got = spk.speak(doc, out_path=wav)
    assert got == wav
    cmd = run.call_args[0][0]
    assert cmd[0] == "/usr/bin/espeak-ng"
    assert "-v" in cmd and "la" in cmd and "-w" in cmd and wav in cmd


def test_speak_missing_engine_raises(nlp):
    doc = nlp("arma")
    spk = nlp.get_pipe("speaker")
    with mock.patch("latincy_ext.speaker.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="espeak-ng not found"):
            spk.speak(doc)


def test_coqui_missing_dep_raises(lookup_file):
    n = spacy.blank("la")
    n.add_pipe("speaker", config={"lookup_path": lookup_file, "backend": "coqui"})
    doc = n("arma")
    with mock.patch.dict("sys.modules", {"TTS.api": None}):
        with pytest.raises(RuntimeError, match="coqui"):
            n.get_pipe("speaker").speak(doc)


# --- serialization ---------------------------------------------------------

def test_bytes_roundtrip(lookup_file):
    n = spacy.blank("la")
    n.add_pipe("speaker", config={"lookup_path": lookup_file, "voice": "la", "rate": 140})
    b = n.get_pipe("speaker").to_bytes()
    n2 = spacy.blank("la")
    spk2 = n2.add_pipe("speaker")
    spk2.from_bytes(b)
    assert spk2.rate == 140
    assert spk2.voice == "la"
