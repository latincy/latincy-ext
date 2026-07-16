"""Tests for the Latin syllabifier / qShape primitives (latincy_ext.syllabify).

Segmentation was cross-checked during development against
``cltk.prosody.lat.Syllabifier`` (Todd Cook, MIT) as an oracle. Cases where we
deliberately differ are called out inline.
"""

import pytest

from latincy_ext.syllabify import qshape, syllabify, syllable_weights


# Cases where we agree with the CLTK oracle (the bulk of behavior).
AGREE = {
    "fuit": ["fu", "it"],
    "libri": ["li", "bri"],            # muta-cum-liquida: br onsets
    "contra": ["con", "tra"],
    "amo": ["a", "mo"],
    "bracchia": ["brac", "chi", "a"],
    "deinde": ["dein", "de"],          # ei diphthong
    "certabant": ["cer", "ta", "bant"],
    "aere": ["ae", "re"],              # ae diphthong
    "mōns": ["mōns"],                  # macron preserved
    "domus": ["do", "mus"],
    "lixa": ["li", "xa"],              # x placement (weight handles ×2 later)
    "asper": ["as", "per"],
    "siccus": ["sic", "cus"],          # doubled consonant splits
    "almus": ["al", "mus"],
    "ambo": ["am", "bo"],
    "anguis": ["an", "guis"],          # gu consonantal
    "arbor": ["ar", "bor"],
    "pulcher": ["pul", "cher"],        # ch digraph
    "sanguen": ["san", "guen"],
    "unguentum": ["un", "guen", "tum"],
    "lingua": ["lin", "gua"],
    "linguā": ["lin", "guā"],
    "languidus": ["lan", "gui", "dus"],
    "suis": ["su", "is"],
    "habui": ["ha", "bu", "i"],
    "habuit": ["ha", "bu", "it"],
    "qui": ["qui"],                    # qu single consonant
    "quibus": ["qui", "bus"],
    "hui": ["hui"],                    # ui-diphthong word
    "cui": ["cui"],
    "huic": ["huic"],
    # metrical spot-checks
    "amīcus": ["a", "mī", "cus"],
    "puella": ["pu", "el", "la"],
    "loquitur": ["lo", "qui", "tur"],
    "arma": ["ar", "ma"],
    "virumque": ["vi", "rum", "que"],
    "cano": ["ca", "no"],
    "primus": ["pri", "mus"],
    "oris": ["o", "ris"],
}


@pytest.mark.parametrize("word,expected", sorted(AGREE.items()))
def test_syllabify_matches_expected(word, expected):
    assert syllabify(word) == expected


# --- deliberate divergences from CLTK -------------------------------------

def test_preserves_original_spelling_not_i_to_j():
    """We keep the input's own letters (i), unlike CLTK which rewrites i->j."""
    assert syllabify("iaculum") == ["ia", "cu", "lum"]  # CLTK: ['ja','cu','lum']


def test_pt_splits_metrically():
    """pt closes the preceding syllable (heavy u); CLTK keeps 'ptus' together."""
    assert syllabify("ruptus") == ["rup", "tus"]  # CLTK: ['ru','ptus']


# --- consonantal i (M3) ----------------------------------------------------

@pytest.mark.parametrize(
    "word,expected",
    [
        # intervocalic V-i-V -> geminate consonantal i (i preserved, not j)
        ("maior", ["ma", "ior"]),
        ("Troiae", ["Tro", "iae"]),
        ("eius", ["e", "ius"]),
        ("cuius", ["cu", "ius"]),
        # prefix-boundary C-i-V (the former xfail)
        ("adiungere", ["ad", "iun", "ge", "re"]),
        ("coniunx", ["con", "iunx"]),
        ("iniuria", ["in", "iu", "ri", "a"]),
        # word-initial consonantal i (unchanged from M1)
        ("iaculum", ["ia", "cu", "lum"]),
    ],
)
def test_consonantal_i(word, expected):
    assert syllabify(word) == expected


def test_consonantal_i_makes_preceding_heavy():
    # geminate i closes the preceding syllable by position
    assert qshape("maior") == "H.H"
    assert qshape("Troiae") == "H.H"
    assert qshape("eius") == "H.H"
    # prefix-boundary: ad is heavy (closed by d), stem i onsets jun
    assert qshape("adiungere") == "H.H.x.x"


def test_consonantal_i_not_overtriggered():
    # i not before a vowel, or a long ī, stays vocalic
    assert syllabify("fuit") == ["fu", "it"]        # i + consonant
    assert syllabify("suis") == ["su", "is"]        # i + consonant
    assert syllabify("audii") == ["au", "di", "i"]  # final i, prev consonant


def test_eo_compounds_stay_vocalic():
    # prefix rule must NOT fire on eo-compounds: the stem i stays a *vowel*
    # (3 syllables), not a consonantal j. Division follows the maximal-onset
    # rule (single consonant onsets the next syllable).
    assert syllabify("abiit") == ["a", "bi", "it"]
    assert syllabify("rediit") == ["re", "di", "it"]
    assert syllabify("exiit") == ["e", "xi", "it"]
    assert syllabify("adiit") == ["a", "di", "it"]


# --- basic invariants ------------------------------------------------------

def test_empty_and_single():
    assert syllabify("") == []
    assert syllabify("a") == ["a"]

@pytest.mark.parametrize("word", sorted(AGREE))
def test_syllables_reconstitute_word(word):
    """Syllable pieces must join back to the exact original string."""
    assert "".join(syllabify(word)) == word


# --- M2: positional weight (H/L/x) -----------------------------------------

# Auto macron-detection: a macron anywhere -> macronized context (unmarked = L);
# no macron -> honest x for open-syllable unknown natura. mcl commons and
# closed-by-position H are independent of that.
QSHAPE_AUTO = {
    "amīcus": "L.H.H",     # a short, mī long, cus closed
    "puella": "x.H.x",     # honest x at the nom/abl sites
    "puellā": "L.H.H",     # ablative resolved by the macron
    "arma": "H.x",         # ar closed, a final open unknown
    "contra": "H.x",       # con closed by ntr
    "mōns": "H",           # long monosyllable
    "patris": "x.H",       # pa common via mute+liquid (t+r)
    "libri": "x.x",        # li common (b+r), bri final open unknown
    "aere": "H.x",         # ae diphthong heavy
}


@pytest.mark.parametrize("word,expected", sorted(QSHAPE_AUTO.items()))
def test_qshape_auto(word, expected):
    assert qshape(word) == expected


def test_qshape_weights_align_with_syllables():
    pairs = syllable_weights("amīcus")
    assert pairs == [("a", "L"), ("mī", "H"), ("cus", "H")]


# The morphosyntactic payoff: quantity distinguishes syncretic forms.
def test_syncretism_tense_venit():
    assert qshape("venit") == "x.H"          # present (unmarked -> honest x)
    assert qshape("vēnit") == "H.H"          # perfect (long ē)


def test_syncretism_case_rosa():
    assert qshape("rosa") == "x.x"           # nom/abl ambiguous, honest
    assert qshape("rosā") == "L.H"           # ablative resolved


# Explicit macronized override: all-short words that carry no macron.
def test_macronized_override_all_short():
    assert qshape("loquitur") == "x.x.H"                    # auto: no macron
    assert qshape("loquitur", macronized=True) == "L.L.H"   # told it's resolved


def test_mcl_common_independent_of_macron():
    # mute+liquid stays common (x) even when told the input is macronized
    assert qshape("patris", macronized=True) == "x.H"
