"""lila_linker — offline LiLa Lemma Bank linking component for LatinCy.

Resolves every content token's lemma to a LiLa URI using a local SQLite
artifact — no network calls at inference time.

Sets these custom extensions on each non-punct/space token:

- ``token._.lila_uri``        — top-ranked LiLa URI, or None on miss
- ``token._.lila_candidates`` — ranked list of all candidate URIs
- ``token._.lila_source``     — resolution path: ``lemma_pos`` | ``lemma`` |
                                 ``wr`` | ``form`` | ``miss``, or ``rule:<name>``
                                 when a v2 disambiguation rule overrode MFS
- ``token._.lila_confidence`` — top candidate's attestation share in [0, 1], or
                                 None when there is no frequency evidence

Resolution order (v1):
    1. (macron_key, UPOS)/(macron_key)  — length-aware, only if the artifact
                                          carries macron keys and a macronized
                                          form is available (see ``use_macron``)
    2. (norm_lemma, UPOS) in lemma_uri  — POS-keyed, handles POS homographs
    3. norm_lemma (any UPOS) in lemma_uri
    4. norm_lemma in wr_uri             — orthographic-variant backbone
    5. norm_form in form_uri            — attestation fallback (see ``form_policy``)

Ambiguity: v1 returns the Most-Frequent-Sense (MFS) URI and exposes the full
ranked candidate list plus a confidence score. Set ``disambiguate=True`` to run
the cheap FEATS/dependency rules in :mod:`latincy_ext.lila_disambiguate` over the
concentrated ambiguous tail (e.g. ``ut`` temporal-vs-purpose by clause mood).

Usage::

    import spacy
    import latincy_ext  # registers the factory

    nlp = spacy.load("la_core_web_lg")
    nlp.add_pipe("lila_linker", config={
        "db_path": "/path/to/lila_linkbank.sqlite",
        "disambiguate": True,
    })

    doc = nlp("Gallia est omnis divisa in partes tres.")
    for t in doc:
        print(t.text, t._.lila_uri, t._.lila_source, t._.lila_confidence)

The ``db_path`` can also be set via the ``LATINCY_LILA_DB`` environment variable.

Licensing note: the LiLa artifact incorporates LASLA data (CC-BY-NC-SA 4.0).
Academic / non-commercial use only when using the enriched artifact.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from spacy.language import Language
from spacy.tokens import Doc, Token

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_HOMOGRAPH_RE = re.compile(r"[#_]?\d+$")
_MACRONS = "āēīōūȳĀĒĪŌŪȲ"


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


def normalize_lemma_macron(s: str) -> str:
    """Length-aware key: like :func:`normalize_lemma` but *preserves* vowel length.

    ``normalize_lemma`` strips every diacritic, collapsing true length-homographs
    (``mālum`` "apple" vs ``malum`` "evil", ``ōs`` vs ``os``) onto one key. This
    variant keeps macrons so those senses stay distinct — provided the artifact
    was built with a matching ``macron_key`` column (see ``REBUILD.md``). Breves
    and other combining marks are still dropped; only macrons are significant.
    """
    if not s:
        return ""
    # NFC so macrons are single codepoints, then drop non-macron combining marks.
    s = unicodedata.normalize("NFC", s)
    kept = []
    for ch in s:
        if ch in _MACRONS:
            kept.append(ch)
        elif not unicodedata.combining(ch):
            # decompose to shed a breve/diaeresis but keep the base letter
            kept.append(_strip_diacritics(ch) or ch)
    s = "".join(kept).lower()
    s = s.replace("v", "u").replace("j", "i")
    s = _HOMOGRAPH_RE.sub("", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

@dataclass
class Resolution:
    uri: Optional[str]
    candidates: List[str] = field(default_factory=list)
    freqs: List[int] = field(default_factory=list)
    kind: Optional[str] = None
    source: str = "miss"
    confidence: Optional[float] = None  # top attestation share in [0,1], or None
    margin: Optional[float] = None      # (top-2nd)/total share, or None


def _sort_key(row):
    uri, kind, freq, is_gold = row
    return (-(is_gold or 0), -(freq or 0), 0 if kind == "lemma" else 1, uri)


def _confidence(freqs: List[int]) -> Tuple[Optional[float], Optional[float]]:
    """(confidence, margin) from ranked candidate freqs.

    - single candidate      → (1.0, 1.0)  (nothing to be ambiguous with)
    - no attestation at all  → (None, None) (backbone-only, no evidence to weigh)
    - otherwise              → top share, and gap to the runner-up as a share
    """
    n = len(freqs)
    if n == 0:
        return None, None
    if n == 1:
        return 1.0, 1.0
    total = sum(freqs)
    if total <= 0:
        return None, None
    top, second = freqs[0], freqs[1]
    return top / total, (top - second) / total


def _resolution(rows, source: str, *, promote: bool = True) -> Resolution:
    ranked = sorted(rows, key=_sort_key)
    uris = [r[0] for r in ranked]
    freqs = [int(r[2] or 0) for r in ranked]
    conf, margin = _confidence(freqs)
    uri = uris[0] if (uris and promote) else None
    return Resolution(uri, uris, freqs, ranked[0][1], source, conf, margin)


class LilaResolver:
    """Offline SQLite-backed LiLa lemma → URI resolver."""

    def __init__(self, db_path: str, *, form_policy: str = "abstain") -> None:
        if form_policy not in ("abstain", "promote"):
            raise ValueError("form_policy must be 'abstain' or 'promote'")
        self.form_policy = form_policy
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.has_macron = self._detect_macron()

    def _detect_macron(self) -> bool:
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(lemma_uri)")}
        return "macron_key" in cols

    def meta(self) -> Dict[str, str]:
        """Return the artifact's ``meta`` table as a dict (empty if absent)."""
        try:
            return {k: v for k, v in self.con.execute("SELECT k, v FROM meta")}
        except sqlite3.OperationalError:
            return {}

    # -- per-table lookups --------------------------------------------------

    def _macron_rows(self, mk: str, upos: str):
        if not self.has_macron:
            return [], False
        if upos:
            rows = self.con.execute(
                "SELECT uri,kind,freq,is_gold FROM lemma_uri WHERE macron_key=? AND upos=?",
                (mk, upos),
            ).fetchall()
            if rows:
                return rows, True
        rows = self.con.execute(
            "SELECT uri,kind,freq,is_gold FROM lemma_uri WHERE macron_key=?", (mk,)
        ).fetchall()
        return rows, False

    def _lemma_rows(self, nk: str, upos: str):
        if upos:
            rows = self.con.execute(
                "SELECT uri,kind,freq,is_gold FROM lemma_uri WHERE norm_key=? AND upos=?",
                (nk, upos),
            ).fetchall()
            if rows:
                return rows, True
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

    # -- public API ---------------------------------------------------------

    def resolve(
        self,
        lemma: str,
        upos: Optional[str] = None,
        form: Optional[str] = None,
        macron: Optional[str] = None,
    ) -> Resolution:
        upos = upos or ""

        # 1. length-aware macron key (only when artifact + input support it)
        if macron and self.has_macron:
            mk = normalize_lemma_macron(macron)
            rows, pos_keyed = self._macron_rows(mk, upos)
            if rows:
                return _resolution(rows, "macron_pos" if pos_keyed else "macron")

        nk = normalize_lemma(lemma)

        # 2/3. length-blind lemma key, POS-first then any-POS
        rows, pos_keyed = self._lemma_rows(nk, upos)
        if rows:
            return _resolution(rows, "lemma_pos" if pos_keyed else "lemma")

        # 4. orthographic-variant backbone
        rows = self._wr_rows(nk)
        if rows:
            return _resolution(rows, "wr")

        # 5. surface-form attestation fallback — abstain or promote per policy
        if form:
            rows = self._form_rows(normalize_form(form))
            if rows:
                return _resolution(rows, "form", promote=self.form_policy == "promote")

        return Resolution(None, [], [], None, "miss")


# ---------------------------------------------------------------------------
# spaCy component
# ---------------------------------------------------------------------------

_EXTS = ("lila_uri", "lila_candidates", "lila_source", "lila_confidence")


@Language.factory(
    "lila_linker",
    default_config={
        "db_path": None,
        "disambiguate": False,
        "form_policy": "abstain",
        "use_macron": False,
    },
    assigns=[
        "token._.lila_uri",
        "token._.lila_candidates",
        "token._.lila_source",
        "token._.lila_confidence",
    ],
)
def make_lila_linker(
    nlp: Language,
    name: str,
    db_path: Optional[str],
    disambiguate: bool,
    form_policy: str,
    use_macron: bool,
) -> "LilaLinker":
    resolved = db_path or os.environ.get("LATINCY_LILA_DB")
    if not resolved:
        raise ValueError(
            "lila_linker requires db_path (pass via config or LATINCY_LILA_DB env var)"
        )
    return LilaLinker(
        resolved,
        disambiguate=disambiguate,
        form_policy=form_policy,
        use_macron=use_macron,
    )


def _macron_signal(t: Token) -> Optional[str]:
    """A macronized surface form for this token, if a macronizer left one."""
    for ext in ("macronized", "orig_text"):
        if t.has_extension(ext):
            val = getattr(t._, ext, None)
            if val and any(c in _MACRONS for c in val):
                return val
    return None


class LilaLinker:
    """spaCy pipeline component that resolves token lemmas to LiLa URIs."""

    def __init__(
        self,
        db_path: str,
        *,
        disambiguate: bool = False,
        form_policy: str = "abstain",
        use_macron: bool = False,
    ) -> None:
        self.db_path = str(db_path)
        self.disambiguate = disambiguate
        self.form_policy = form_policy
        self.use_macron = use_macron
        self.resolver = LilaResolver(self.db_path, form_policy=form_policy)
        for ext in _EXTS:
            if not Token.has_extension(ext):
                Token.set_extension(ext, default=None)

    @property
    def meta_info(self) -> Dict[str, str]:
        """Artifact provenance (backbone source/license, version, build time)."""
        return self.resolver.meta()

    def __call__(self, doc: Doc) -> Doc:
        # Import here so the resolver stays importable without the rule module.
        from .lila_disambiguate import disambiguate as apply_rules

        for t in doc:
            if t.is_punct or t.is_space:
                continue
            macron = _macron_signal(t) if self.use_macron else None
            res = self.resolver.resolve(t.lemma_, t.pos_, t.text, macron=macron)
            uri, source = res.uri, res.source
            candidates = res.candidates

            if self.disambiguate and res.uri is not None:
                picked, rule = apply_rules(t, res)
                if rule is not None:
                    uri = picked
                    source = f"rule:{rule}"
                    # surface the rule's pick at the head of the candidate list
                    candidates = [picked] + [c for c in candidates if c != picked]

            t._.lila_uri = uri
            t._.lila_candidates = candidates
            t._.lila_source = source
            t._.lila_confidence = res.confidence
        return doc

    def to_disk(self, path, *, exclude=()):
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "cfg.json").write_text(
            json.dumps(
                {
                    "db_path": self.db_path,
                    "disambiguate": self.disambiguate,
                    "form_policy": self.form_policy,
                    "use_macron": self.use_macron,
                }
            ),
            encoding="utf-8",
        )

    def from_disk(self, path, *, exclude=()):
        cfg = json.loads((Path(path) / "cfg.json").read_text(encoding="utf-8"))
        self.db_path = cfg["db_path"]
        self.disambiguate = cfg.get("disambiguate", False)
        self.form_policy = cfg.get("form_policy", "abstain")
        self.use_macron = cfg.get("use_macron", False)
        self.resolver = LilaResolver(self.db_path, form_policy=self.form_policy)
        return self
