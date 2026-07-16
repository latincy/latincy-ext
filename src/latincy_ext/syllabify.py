"""Latin syllabifier and positional-weight (qShape) primitives.

Pure-Python, dependency-free. Splits a (optionally macronized) Latin word into
syllables and — in a later milestone — assigns each syllable a quantity
``H`` / ``L`` / ``x``.

Design notes
------------
- The syllabification *rules* are standard Latin practice (single intervocalic
  consonant onsets the following syllable; two consonants split unless they form
  a mute+liquid onset; diphthongs are single nuclei; ``x``/``z`` are double
  consonants).
- Boundaries are computed as character offsets into the *original* string, so
  the returned syllables preserve the input's case and macrons exactly.

Relationship to CLTK
--------------------
This module is an **independent reimplementation** of Latin syllabification. Its
rule behavior was developed and validated against the Classical Language Toolkit
(CLTK) syllabifier, ``cltk.prosody.lat.Syllabifier`` (author: Todd Cook; CLTK,
MIT License — https://github.com/cltk/cltk), used purely as a *test oracle*: the
test suite pins cases where we agree with CLTK and the cases where we diverge.

It is not a port or refactor of CLTK source — **no CLTK code is imported or
vendored** (CLTK is not a dependency of this package), and the implementation
uses its own architecture: a span-preserving ``_Unit`` tokenizer that keeps the
input's original orthography rather than rewriting ``i→j``/``u→v`` as CLTK does.
Divergences are deliberate and metrically motivated — e.g. we split ``rup·tus``
(heavy first syllable) where CLTK gives ``ru·ptus``, and we keep ``ia·cu·lum``
where CLTK gives ``ja·cu·lum``. CLTK is credited in the README and Bibliography.

If any of this understates the lineage (e.g. the algorithm was in fact adapted
from CLTK source), upgrade this note to a derivative-work attribution and add a
NOTICE reproducing CLTK's MIT copyright + permission notice.

This module is a plain utility; the spaCy ``qshaper`` component that will consume
it is a separate, later step.
"""

from __future__ import annotations

# --- prosody constants -----------------------------------------------------

# Plain (short-or-unmarked) vowels, including the pre-decomposed diaeresis forms
# CLTK carries. Macron-bearing vowels are listed separately as "long by nature".
PLAIN_VOWELS = set("aeiouyAEIOUYäëïöüÿÄËÏÖÜŸ")
LONG_VOWELS = set("āēīōūȳĀĒĪŌŪȲ")  # long by nature (macron)
VOWELS = PLAIN_VOWELS | LONG_VOWELS

# Vocalic nuclei. ae/au/oe are always diphthongs. ei is a diphthong only when
# NOT before a vowel (deinde -> dein·de, but eius -> e·ius with consonantal i).
# eu and ui are diphthongs only in short closed word lists; elsewhere the two
# vowels are separate (deus, meus; fu·it), and an intervocalic i is consonantal.
ALWAYS_DIPHTHONGS = {"ae", "au", "oe"}
EU_DIPHTHONG_WORDS = {"seu", "ceu", "neu", "heu", "Seu", "Ceu", "Neu", "Heu"}
UI_DIPHTHONG_WORDS = {"cui", "hui", "huic", "Cui", "Hui", "Huic"}

# Consonant-final (and common productive) prefixes after which a stem-initial
# ``i`` + vowel is consonantal: ad+iungere -> ad·jun·ge·re, con+iunx, in+iuria.
# Conservative on purpose — only triggers on an ``i`` immediately followed by a
# vowel, so e.g. "inire" (i+r) and "India" (i+d) are untouched.
CONSONANTAL_I_PREFIXES = (
    "circum", "trans", "inter", "con", "com", "dis", "sub", "per", "red",
    "ad", "ab", "ob", "in", "ex",
)
# The prefix rule only fires before a *back* vowel (iungere, iuria, iacio). This
# is what keeps the very common eo-compounds vocalic: abiit, rediit, exiit,
# adiit all have i + front vowel (i/e) and must stay ab·i·it, not ab·jit.
_BACK_VOWELS = set("aouāōūAOUĀŌŪ")

# Consonant digraphs that act as a single onset consonant.
CONSONANT_DIGRAPHS = {"ch", "ph", "th", "rh", "Ch", "Ph", "Th", "Rh"}

MUTES = set("bcdfgptBCDFGPT")      # stops (+f) for muta-cum-liquida
LIQUIDS_MCL = set("lrLR")          # only l, r make a true mute+liquid onset
DOUBLE_CONSONANTS = set("xzXZ")    # x (=cs), z (=dz): count as two for weight

_CONSONANT_CHARS = set("bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ")


def _is_vowel_char(ch: str) -> bool:
    return ch in VOWELS


# --- unit tokenizer --------------------------------------------------------

class _Unit:
    """A vowel-nucleus or consonant unit with its span in the original word.

    ``geminate`` marks an intervocalic consonantal ``i`` (``maior`` -> maj·jor):
    it onsets the following syllable like one consonant but counts as *two* for
    the preceding syllable's position (so the preceding nucleus is heavy).
    """

    __slots__ = ("text", "start", "end", "is_vowel", "geminate")

    def __init__(
        self, text: str, start: int, end: int, is_vowel: bool, geminate: bool = False
    ):
        self.text = text
        self.start = start
        self.end = end
        self.is_vowel = is_vowel
        self.geminate = geminate

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        kind = "V" if self.is_vowel else "C"
        return f"{kind}({self.text!r})"


def _consonantal_i_prefix(word: str, i: int) -> bool:
    """True if the ``i`` at position ``i`` sits right after a known prefix."""
    head = word[:i].lower()
    return head in CONSONANTAL_I_PREFIXES


def _units(word: str) -> list[_Unit]:
    """Tokenize a word into ordered vowel-nucleus and consonant units.

    Handles: diphthongs, macron vowels, ``qu``/``gu``(+vowel) as single onset
    consonants, consonant digraphs (ch/ph/th/rh), and word-initial consonantal
    ``i`` (before a vowel) treated as a consonant.
    """
    lower = word.lower()
    n = len(word)
    units: list[_Unit] = []
    i = 0
    while i < n:
        ch = word[i]
        lo = lower[i]

        # qu / gu(+vowel): single consonant unit (u is consonantal here)
        if lo in ("q", "g") and i + 1 < n and lower[i + 1] == "u":
            if lo == "q":
                units.append(_Unit(word[i : i + 2], i, i + 2, False))
                i += 2
                continue
            # gu is consonantal only before a vowel (lingua, sanguis)
            if i + 2 < n and lower[i + 2] in {c.lower() for c in VOWELS}:
                units.append(_Unit(word[i : i + 2], i, i + 2, False))
                i += 2
                continue

        # consonant digraphs ch/ph/th/rh
        if word[i : i + 2] in CONSONANT_DIGRAPHS:
            units.append(_Unit(word[i : i + 2], i, i + 2, False))
            i += 2
            continue

        # consonantal i: plain "i" (not macron ī) before a vowel, either
        #   - word-initial (iam, iustus),
        #   - intervocalic (maior, Troiae, eius) -> geminate (heavy prev), or
        #   - at a prefix boundary (ad+iungere, con+iunx, in+iuria).
        if lo == "i" and i + 1 < n and _is_vowel_char(word[i + 1]):
            prev_is_vowel = i > 0 and _is_vowel_char(word[i - 1])
            if i == 0:
                units.append(_Unit(word[i], i, i + 1, False))
                i += 1
                continue
            if prev_is_vowel:  # intervocalic -> geminate consonantal i
                units.append(_Unit(word[i], i, i + 1, False, geminate=True))
                i += 1
                continue
            if word[i + 1] in _BACK_VOWELS and _consonantal_i_prefix(word, i):
                # prefix boundary before a back vowel (ad+iungere, con+iunx)
                units.append(_Unit(word[i], i, i + 1, False))
                i += 1
                continue

        # diphthongs (two-vowel nucleus)
        two = lower[i : i + 2]
        if two in ALWAYS_DIPHTHONGS:
            units.append(_Unit(word[i : i + 2], i, i + 2, True))
            i += 2
            continue
        # ei: diphthong only when NOT before a vowel (deinde vs. eius)
        if two == "ei" and not (i + 2 < n and _is_vowel_char(word[i + 2])):
            units.append(_Unit(word[i : i + 2], i, i + 2, True))
            i += 2
            continue
        if two == "eu" and word in EU_DIPHTHONG_WORDS:
            units.append(_Unit(word[i : i + 2], i, i + 2, True))
            i += 2
            continue
        if two == "ui" and word in UI_DIPHTHONG_WORDS:
            units.append(_Unit(word[i : i + 2], i, i + 2, True))
            i += 2
            continue

        # single vowel nucleus
        if _is_vowel_char(ch):
            units.append(_Unit(ch, i, i + 1, True))
            i += 1
            continue

        # single consonant (h included; x/z are single units, weight handles ×2)
        units.append(_Unit(ch, i, i + 1, False))
        i += 1

    return units


# --- syllabification -------------------------------------------------------

def _is_mcl(c1: _Unit, c2: _Unit) -> bool:
    """True if the two consonant units form a mute+liquid (l/r) onset."""
    if len(c1.text) != 1 or c1.text not in MUTES:
        return False
    return c2.text in LIQUIDS_MCL


def _boundaries(units: list[_Unit]) -> list[int]:
    """Return character offsets at which to cut the word into syllables.

    Assigns each inter-vocalic consonant run: 1 consonant -> onset of the next
    syllable; 2 -> split unless mute+liquid (then both onset); 3+ -> last (or
    trailing mcl pair) onsets the next, the rest close the preceding.
    """
    vowel_idxs = [k for k, u in enumerate(units) if u.is_vowel]
    if len(vowel_idxs) <= 1:
        return []

    cuts: list[int] = []
    for a, b in zip(vowel_idxs, vowel_idxs[1:]):
        run = units[a + 1 : b]  # consonant units between two nuclei
        m = len(run)
        if m == 0:
            # hiatus: cut right after the first vowel (fu·it, de·us)
            cut_after = a
        elif m == 1:
            cut_after = a  # single consonant onsets next syllable
        else:
            # 2+ consonants: does the FINAL pair form a mute+liquid onset?
            if _is_mcl(run[-2], run[-1]):
                cut_after = b - 3  # everything up to before the mcl pair closes
            else:
                cut_after = b - 2  # last consonant onsets next; rest close
        cuts.append(units[cut_after].end)
    return cuts


def syllabify(word: str) -> list[str]:
    """Split a Latin word into a list of syllable strings.

    >>> syllabify("amo")
    ['a', 'mo']
    >>> syllabify("amīcus")
    ['a', 'mī', 'cus']
    >>> syllabify("puella")
    ['pu', 'el', 'la']
    >>> syllabify("contra")
    ['con', 'tra']
    >>> syllabify("libri")
    ['li', 'bri']
    >>> syllabify("mōns")
    ['mōns']
    """
    if not word:
        return []
    units = _units(word)
    if not any(u.is_vowel for u in units):
        return [word]
    cuts = _boundaries(units)
    if not cuts:
        return [word]
    pieces: list[str] = []
    prev = 0
    for c in cuts:
        pieces.append(word[prev:c])
        prev = c
    pieces.append(word[prev:])
    return [p for p in pieces if p]


# --- positional weight (qShape) --------------------------------------------

# Weight symbols
HEAVY = "H"       # long by nature, diphthong, or closed by position
LIGHT = "L"       # open syllable, short by nature (known)
COMMON = "x"      # muta-cum-liquida common, OR open syllable of unknown natura


def _effective_consonant_count(run: list[_Unit]) -> int:
    """Count consonants for *position*, treating x/z as 2 and standalone h as 0.

    Digraphs (ch/ph/th/rh) and qu/gu count as one; geminates are two single
    units and so count two.
    """
    total = 0
    for u in run:
        if u.text in ("h", "H"):
            continue  # h is transparent to metrical position
        if u.geminate or u.text in DOUBLE_CONSONANTS:
            total += 2  # geminate consonantal i, or x/z
        else:
            total += 1
    return total


def _nucleus_is_long(u: _Unit) -> bool:
    """A nucleus is long by nature if it is a diphthong or bears a macron."""
    if len(u.text) == 2:  # diphthong
        return True
    return any(c in LONG_VOWELS for c in u.text)


def syllable_weights(
    word: str, macronized: bool | None = None
) -> list[tuple[str, str]]:
    """Return ``[(syllable, weight)]`` with weight in ``H`` / ``L`` / ``x``.

    ``macronized`` controls how an *unmarked* vowel in an open syllable is
    scored:

    - ``True``  -> short by nature -> ``L`` (absence of a macron is signal).
    - ``False`` -> natura unknown -> ``x`` (honest).
    - ``None`` (default) -> auto: treat the word as macronized iff it contains
      at least one macron. So a partially-macronized form (the macronizer ran)
      yields full ``H``/``L``, while a plain unmarked form yields honest ``x``.

    Weights independent of ``macronized``: long-by-nature/diphthong/closed ->
    ``H``; muta-cum-liquida common syllables -> ``x``.

    >>> qshape("amīcus")   # has a macron -> macronized context
    'L.H.H'
    >>> qshape("puella")   # no macron -> honest x at the nom/abl sites
    'x.H.x'
    >>> qshape("puellā")   # ablative resolved by the macron
    'L.H.H'
    >>> qshape("contra")
    'H.x'
    """
    units = _units(word)
    nuclei = [k for k, u in enumerate(units) if u.is_vowel]
    if macronized is None:
        macronized = any(c in LONG_VOWELS for c in word)

    weights: list[str] = []
    for j, k in enumerate(nuclei):
        u = units[k]
        nxt = nuclei[j + 1] if j + 1 < len(nuclei) else len(units)
        run = units[k + 1 : nxt]
        is_last = j == len(nuclei) - 1

        if _nucleus_is_long(u):
            weights.append(HEAVY)
            continue

        eff = _effective_consonant_count(run)
        if is_last:
            # all trailing consonants are coda: any real consonant closes it
            weights.append(HEAVY if eff >= 1 else (LIGHT if macronized else COMMON))
        elif eff >= 2:
            if len(run) == 2 and _is_mcl(run[0], run[1]):
                weights.append(COMMON)  # mute+liquid: common
            else:
                weights.append(HEAVY)   # closed by position
        else:
            # open syllable (0 or 1 following consonant)
            weights.append(LIGHT if macronized else COMMON)

    sylls = syllabify(word)
    if len(sylls) != len(weights):  # pragma: no cover - defensive
        # nucleus count drives both; mismatch only on vowel-less fragments
        return [(s, "?") for s in sylls]
    return list(zip(sylls, weights))


def qshape(word: str, macronized: bool | None = None) -> str:
    """Dotted qShape string, e.g. ``"L.H.H"``. See :func:`syllable_weights`."""
    return ".".join(w for _, w in syllable_weights(word, macronized))
