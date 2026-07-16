"""syllabifier — spaCy pipeline component for Latin syllabification + qShape.

Wraps the pure :mod:`latincy_ext.syllabify` utility and exposes its output on
each token:

- ``token._.syllables`` : list[str] of syllables (``["a", "mī", "cus"]``)
- ``token._.qshape``    : dotted quantity string ``H``/``L``/``x`` (``"L.H.H"``)

Natura (vowel length by nature) is taken, in priority order:

1. the macronizer's in-context output ``token._.macronized`` (best — resolves
   syncretism from context) when ``use_macronizer_output`` is on;
2. a pre-macronized surface form in ``token._.orig_text``;
3. *(reserved seam)* a static kaikki/Wiktionary macronized-form lexicon via
   ``lookup_path`` — see the note on :meth:`SyllabifierComponent._natura_form`;
4. otherwise the bare token text, which yields honest ``x`` at open syllables of
   unknown natura.

These are additive annotations, never overrides — same non-destructive contract
as :mod:`latincy_ext.macron_morph`. Chain after the macronizer for plain text::

    import spacy, latincy_ext  # registers the factory
    nlp = spacy.load("la_core_web_lg")
    nlp.add_pipe("macronizer", ...)          # sets token._.macronized
    nlp.add_pipe("syllabifier", last=True)   # reads it, sets qshape

Span-level metrical shape (``mShape``: elision, brevis-in-longo, clausulae) is a
separate, later component and is deliberately out of scope here.

Provenance: the wrapped :mod:`latincy_ext.syllabify` rules are an independent
reimplementation validated against CLTK's ``cltk.prosody.lat.Syllabifier``
(Todd Cook; CLTK, MIT) as a test oracle — see that module's "Relationship to
CLTK" note. No CLTK code is imported or vendored.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Optional

from spacy.language import Language
from spacy.tokens import Doc, Token

from latincy_ext.syllabify import syllable_weights

MACRONS = frozenset("āēīōūȳĀĒĪŌŪȲ")


@Language.factory(
    "syllabifier",
    default_config={
        "use_macronizer_output": True,
        "lookup_path": None,
        "macronized": None,
    },
    assigns=["token._.syllables", "token._.qshape"],
)
def create_syllabifier(
    nlp: Language,
    name: str,
    use_macronizer_output: bool,
    lookup_path: Optional[str],
    macronized: Optional[bool],
) -> "SyllabifierComponent":
    return SyllabifierComponent(
        nlp,
        name,
        use_macronizer_output=use_macronizer_output,
        lookup_path=lookup_path,
        macronized=macronized,
    )


class SyllabifierComponent:
    """Sets ``token._.syllables`` and ``token._.qshape`` for each token."""

    def __init__(
        self,
        nlp: Language,
        name: str,
        *,
        use_macronizer_output: bool = True,
        lookup_path: Optional[str | Path] = None,
        macronized: Optional[bool] = None,
    ) -> None:
        self.name = name
        self.use_macronizer_output = use_macronizer_output
        self.macronized = macronized
        self._lookup_path = lookup_path
        self._lookup: dict[str, list[dict]] = {}
        self._loaded = False

        if not Token.has_extension("syllables"):
            Token.set_extension("syllables", default=None)
        if not Token.has_extension("qshape"):
            Token.set_extension("qshape", default="")

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._lookup_path:
            path = Path(self._lookup_path)
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as f:
                self._lookup = json.load(f)
        self._loaded = True

    def _natura_form(self, token: Token) -> str:
        """Return the best macronized form to scan, falling back to raw text.

        Priority: macronizer output -> pre-macronized orig_text -> bare text.

        The ``lookup_path`` static lexicon is intentionally *not* consulted for
        natura yet: the kaikki table keys only macron-bearing forms, so a bare
        surface form like ``rosa`` (which is nom ``rosă`` OR abl ``rosā``) has no
        safe single macronization — applying the ablative macron would fabricate
        the very case distinction qShape must leave as ``x``. Resolving this
        needs the same position-wise intersection (and an unmacronized-reading
        table) that ``macron_morph._resolve_unmarked`` documents as future work.
        Until then, honest ``x`` from the bare form is the correct behavior.
        """
        if self.use_macronizer_output:
            macronized = getattr(token._, "macronized", None)
            if macronized and any(c in MACRONS for c in macronized):
                return macronized
            orig = getattr(token._, "orig_text", None)
            if orig and any(c in MACRONS for c in orig):
                return orig
        return token.text

    def __call__(self, doc: Doc) -> Doc:
        self._ensure_loaded()
        for token in doc:
            if token.is_punct or token.is_space:
                continue
            form = self._natura_form(token)
            pairs = syllable_weights(form, self.macronized)
            token._.syllables = [s for s, _ in pairs]
            token._.qshape = ".".join(w for _, w in pairs)
        return doc

    # --- serialization (mirrors macron_morph) ------------------------------

    def to_disk(self, path: str, *, exclude: tuple = ()) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        cfg: dict = {
            "use_macronizer_output": self.use_macronizer_output,
            "macronized": self.macronized,
        }
        if self._lookup_path:
            cfg["lookup_path"] = str(self._lookup_path)
        with open(path / "config.json", "w") as f:
            json.dump(cfg, f)

    def from_disk(self, path: str, *, exclude: tuple = ()) -> "SyllabifierComponent":
        cfg_file = Path(path) / "config.json"
        if cfg_file.exists():
            with open(cfg_file) as f:
                cfg = json.load(f)
            self.use_macronizer_output = cfg.get("use_macronizer_output", True)
            self.macronized = cfg.get("macronized")
            if cfg.get("lookup_path"):
                self._lookup_path = cfg["lookup_path"]
                self._loaded = False
        return self

    def to_bytes(self, *, exclude: tuple = ()) -> bytes:
        return json.dumps(
            {
                "use_macronizer_output": self.use_macronizer_output,
                "macronized": self.macronized,
                "lookup_path": str(self._lookup_path) if self._lookup_path else None,
            }
        ).encode("utf-8")

    def from_bytes(self, data: bytes, *, exclude: tuple = ()) -> "SyllabifierComponent":
        if data:
            d = json.loads(data.decode("utf-8"))
            self.use_macronizer_output = d.get("use_macronizer_output", True)
            self.macronized = d.get("macronized")
            if d.get("lookup_path"):
                self._lookup_path = d["lookup_path"]
                self._loaded = False
        return self
