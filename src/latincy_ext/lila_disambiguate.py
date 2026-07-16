"""lila_disambiguate — cheap FEATS/dependency rules over the ambiguous tail.

The v1 linker returns the Most-Frequent-Sense (MFS) URI for every
``(lemma, UPOS)`` key. On the held-out corpus MFS already scores ~99% top-1,
and the residual error is small **and concentrated**: a handful of high-frequency
function words whose competing LiLa senses are decidable from information the
pipeline has *already computed* — morphological features and the dependency tree
— not from lexical semantics. A trained biencoder is overkill for that tail; a
handful of rules beats it at near-zero cost.

Each rule is keyed by the normalized ``(norm_lemma, upos)`` and receives the
spaCy ``Token`` plus the v1 :class:`~latincy_ext.lila_linker.Resolution`. It
returns a URI to override the MFS pick, or ``None`` to keep MFS. A rule may only
return a URI that is already among ``resolution.candidates`` — it disambiguates,
it never invents a link absent from *this* artifact.

Grounding note (``ut``): the subjunctive/indicative split of the two live ``ut``
senses was confirmed against the LASLA gold corpus, not hand-asserted — URI
``…/130906`` co-occurs with subjunctive clause verbs (purpose/result/substantive,
the MFS pick), ``…/130905`` with indicatives (temporal "when" / comparative
"as"). See ``evaluations/lila_linker/rule_eval.py`` for the MFS-relative check.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from spacy.tokens import Token

from .lila_linker import normalize_lemma

# ---------------------------------------------------------------------------
# LiLa URIs referenced by rules (full form so a rule is self-documenting)
# ---------------------------------------------------------------------------

_LEMMA = "http://lila-erc.eu/data/id/lemma/"
UT_TEMPORAL = _LEMMA + "130905"   # ut + indicative: temporal / comparative
UT_PURPOSE = _LEMMA + "130906"    # ut + subjunctive: purpose / result (MFS)


# ---------------------------------------------------------------------------
# Feature helpers
# ---------------------------------------------------------------------------

def _mood(tok: Token) -> Optional[str]:
    """First Mood value on a token, or None."""
    vals = tok.morph.get("Mood")
    return vals[0] if vals else None


def _is_finite_verb(tok: Token) -> bool:
    return tok.pos_ in ("VERB", "AUX") and "Fin" in tok.morph.get("VerbForm")


def governed_mood(mark: Token) -> Optional[str]:
    """Mood of the clause verb a subordinator governs.

    A subordinating ``ut``/``cum``/… normally attaches as ``mark`` to its clause
    predicate, so ``token.head`` *is* the governed verb. Fall back to the nearest
    finite verb to the right in the same sentence when the head carries no mood
    (e.g. a mis-parse, or a verbless matrix).
    """
    head = mark.head
    if head is not None and head is not mark:
        m = _mood(head)
        if m:
            return m
    # Fallback needs sentence bounds; skip it on docs without sentence annotation.
    if not mark.doc.has_annotation("SENT_START"):
        return None
    for tok in mark.doc[mark.i + 1 : mark.sent.end]:
        if _is_finite_verb(tok):
            m = _mood(tok)
            if m:
                return m
    return None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _ut_by_mood(tok: Token, res) -> Optional[str]:
    """ut: indicative clause → temporal/comparative; else keep MFS (purpose)."""
    if UT_TEMPORAL not in res.candidates:
        return None  # the minority sense isn't even attested in this artifact
    return UT_TEMPORAL if governed_mood(tok) == "Ind" else None


# (norm_lemma, upos) -> (rule_name, fn)
RULES: Dict[Tuple[str, str], Tuple[str, Callable[[Token, object], Optional[str]]]] = {
    ("ut", "SCONJ"): ("ut_mood", _ut_by_mood),
}


def disambiguate(tok: Token, res) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(uri, rule_name)``.

    ``rule_name`` is None when no rule applies or the rule defers to MFS, in
    which case ``uri`` is the unchanged ``res.uri``.
    """
    entry = RULES.get((normalize_lemma(tok.lemma_), tok.pos_))
    if entry is None:
        return res.uri, None
    name, fn = entry
    pick = fn(tok, res)
    if pick and pick != res.uri and pick in res.candidates:
        return pick, name
    return res.uri, None
