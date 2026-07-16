"""speaker — spaCy pipeline component that speaks Latin from IPA annotations.

A proof-of-concept text-to-speech component. It attaches a Classical-Latin IPA
transcription to each token (``token._.ipa``, looked up from the kaikki-derived
prosody table built by ``latincy-words/scripts/extract_ipa_prosody.py``) and then
synthesizes audio for a ``Doc`` / ``Span`` — driven by that IPA rather than by a
re-guess from the spelling.

Two design commitments:

- **IPA-driven.** Where a token has an IPA reading, we map it to the backend's
  phoneme alphabet and synthesize *those phones* — carrying vowel **length**
  (``ː``) through to the audio, which is exactly the distinction most Latin TTS
  loses. Tokens without an IPA reading fall back to the backend's own Latin G2P
  on the surface text, so an utterance always speaks.
- **Load-safe.** Importing/adding the component never requires a TTS engine to be
  installed; the engine is only needed when you actually call :meth:`speak`.

Backends (``backend`` config):

- ``"espeak-ng"`` (default) — shells out to the ``espeak-ng`` binary. Tiny,
  offline, and accepts phoneme input directly (Kirshenbaum ``[[...]]``). Install
  with e.g. ``brew install espeak-ng``. Robotic but genuinely phoneme-accurate.
- ``"coqui"`` — optional, higher-quality neural VITS via the ``TTS`` package and a
  donor-language voice. Heavier (torch + model download) and its phoneme handling
  is model-dependent; treated as experimental. ``pip/uv add TTS`` to enable.

Pipeline position: after ``syllabifier`` (for the qShape syllable count used to
disambiguate multi-reading IPA) and, on the ``lg`` model, after ``uv_normalizer``
(so ``token._.uv_normalized`` carries the classical ``v``-spelling used as the
lookup key)::

    import spacy, latincy_ext
    nlp = spacy.load("la_core_web_lg")
    nlp.add_pipe("syllabifier")
    nlp.add_pipe("speaker", config={"lookup_path": ".../latin-forms-ipa-prosody.json"})
    doc = nlp("Arma virumque cano")
    nlp.get_pipe("speaker").speak(doc, out_path="aeneid.wav")
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from spacy.language import Language
from spacy.tokens import Doc, Span, Token

_MACRON_STRIP = str.maketrans("āēīōūȳĀĒĪŌŪȲ", "aeiouyAEIOUY")

# ---------------------------------------------------------------------------
# IPA → espeak-ng Kirshenbaum (ASCII phoneme) mapping for Classical Latin.
#
# Best-effort and deliberately small: Latin's phone inventory is compact. This
# is the tunable knob if the audio needs work — approximations are marked.
# ---------------------------------------------------------------------------

# Multi-codepoint sequences must be tried before single chars.
_IPA_MULTI = [
    ("kʷ", "kw"), ("ɡʷ", "gw"),
    ("pʰ", "p"), ("tʰ", "t"), ("kʰ", "k"),   # aspiration dropped (approx.)
    ("t̪", "t"), ("d̪", "d"),                  # dental → plain (approx.)
]
_IPA_SINGLE = {
    # vowels
    "a": "a", "e": "e", "ɛ": "E", "i": "i", "ɪ": "I",
    "o": "o", "ɔ": "O", "u": "u", "ʊ": "U", "y": "y", "ø": "Y", "œ": "W",
    "ə": "@", "æ": "&", "ɑ": "A",
    # consonants
    "p": "p", "b": "b", "t": "t", "d": "d", "k": "k", "ɡ": "g", "g": "g",
    "f": "f", "v": "v", "s": "s", "z": "z", "h": "h",
    "l": "l", "ɫ": "l", "r": "r", "ɾ": "r", "m": "m", "n": "n", "ŋ": "N",
    "w": "w", "j": "j",
    # suprasegmentals
    "ː": ":",     # length → Kirshenbaum length
    "ˈ": "'",     # primary stress
    "ˌ": ",",     # secondary stress
}
# Combining diacritics / boundaries dropped outright.
_IPA_DROP = frozenset(".̯̪ʲ̥̬͡‿ ")   # incl. syllable dot, non-syllabic glide, dental, etc.


def ipa_to_kirshenbaum(ipa: str) -> str:
    """Map a Classical-Latin IPA string to espeak-ng Kirshenbaum phonemes.

    Best-effort: unknown symbols are dropped. Length (``ː``) and stress are
    preserved; the diphthong glide mark is dropped so ``au̯`` → ``aU``-like
    sequences fall out of the adjacent vowels.
    """
    ipa = ipa.strip().strip("[]/")
    for src, dst in _IPA_MULTI:
        ipa = ipa.replace(src, dst)
    out = []
    for ch in ipa:
        if ch in _IPA_SINGLE:
            out.append(_IPA_SINGLE[ch])
        elif ch in _IPA_DROP:
            continue
        # else: unknown → drop silently
    return "".join(out)


@Language.factory(
    "speaker",
    default_config={
        "lookup_path": None,
        "backend": "espeak-ng",
        "voice": "la",
        "use_ipa": True,
        "rate": 120,          # words/min; espeak's native ~175 is too fast for Latin
        "coqui_model": "tts_models/es/css10/vits",
    },
    assigns=["token._.ipa"],
)
def create_speaker(
    nlp: Language,
    name: str,
    lookup_path: Optional[str],
    backend: str,
    voice: str,
    use_ipa: bool,
    rate: Optional[int],
    coqui_model: str,
) -> "SpeakerComponent":
    return SpeakerComponent(
        nlp, name,
        lookup_path=lookup_path, backend=backend, voice=voice,
        use_ipa=use_ipa, rate=rate, coqui_model=coqui_model,
    )


class SpeakerComponent:
    """Attaches ``token._.ipa`` and synthesizes Latin speech from it."""

    def __init__(
        self,
        nlp: Language,
        name: str,
        *,
        lookup_path: Optional[str | Path] = None,
        backend: str = "espeak-ng",
        voice: str = "la",
        use_ipa: bool = True,
        rate: Optional[int] = 120,
        coqui_model: str = "tts_models/es/css10/vits",
    ) -> None:
        self.name = name
        self.backend = backend
        self.voice = voice
        self.use_ipa = use_ipa
        self.rate = rate
        self.coqui_model = coqui_model
        # lookup_path may also come from the environment (mirrors macron_morph).
        self._lookup_path = lookup_path or os.getenv("LATINCY_IPA_LOOKUP")
        self._lookup: dict[str, list[dict]] = {}
        self._loaded = False
        self._coqui = None  # lazily constructed TTS instance

        if not Token.has_extension("ipa"):
            Token.set_extension("ipa", default=None)

    # --- IPA lookup / annotation ------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._lookup_path:
            path = Path(self._lookup_path)
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as f:
                self._lookup = json.load(f)
        self._loaded = True

    def _bare(self, token: Token) -> str:
        """Lookup key: classical v-spelling if the pipeline set it, else text."""
        form = getattr(token._, "uv_normalized", None) or token.text
        return form.translate(_MACRON_STRIP).lower()

    def _lookup_ipa(self, token: Token) -> Optional[str]:
        readings = self._lookup.get(self._bare(token))
        if not readings:
            return None
        # Prefer the reading whose syllable count matches qShape, if available.
        qshape = getattr(token._, "qshape", "") or ""
        if qshape:
            n = qshape.count(".") + 1
            for r in readings:
                if r["natura"].count(".") + 1 == n:
                    return r["ipa"]
        return readings[0]["ipa"]

    def __call__(self, doc: Doc) -> Doc:
        self._ensure_loaded()
        for token in doc:
            if token.is_punct or token.is_space:
                continue
            token._.ipa = self._lookup_ipa(token)
        return doc

    # --- synthesis ---------------------------------------------------------

    def _units(self, obj: Doc | Span) -> Iterable[Token]:
        return (t for t in obj if not (t.is_punct or t.is_space))

    def espeak_input(self, obj: Doc | Span, use_ipa: Optional[bool] = None) -> str:
        """Assemble the espeak-ng input string: phoneme blocks + text fallback.

        A token with an IPA reading becomes a Kirshenbaum ``[[...]]`` block; a
        token without one is passed as plain text for espeak's own Latin G2P.
        """
        use_ipa = self.use_ipa if use_ipa is None else use_ipa
        parts = []
        for t in self._units(obj):
            ipa = getattr(t._, "ipa", None)
            if use_ipa and ipa:
                kb = ipa_to_kirshenbaum(ipa)
                parts.append(f"[[{kb}]]" if kb else t.text)
            else:
                parts.append(t.text)
        return " ".join(parts)

    def speak(
        self,
        obj: Doc | Span,
        out_path: Optional[str] = None,
        *,
        play: bool = False,
        use_ipa: Optional[bool] = None,
        dry_run: bool = False,
    ) -> str:
        """Synthesize ``obj`` to a WAV file and return its path.

        ``dry_run=True`` skips synthesis and returns the assembled backend input
        (handy for tests / inspection). ``play=True`` also plays through the
        default output device (espeak-ng backend only).
        """
        self._ensure_loaded()
        if self.backend == "espeak-ng":
            return self._speak_espeak(obj, out_path, play=play, use_ipa=use_ipa,
                                      dry_run=dry_run)
        if self.backend == "coqui":
            return self._speak_coqui(obj, out_path, use_ipa=use_ipa, dry_run=dry_run)
        raise ValueError(f"unknown backend: {self.backend!r}")

    def _speak_espeak(self, obj, out_path, *, play, use_ipa, dry_run) -> str:
        text = self.espeak_input(obj, use_ipa=use_ipa)
        if dry_run:
            return text
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not exe:
            raise RuntimeError(
                "espeak-ng not found on PATH. Install it (e.g. `brew install "
                "espeak-ng` / `apt install espeak-ng`) or use backend='coqui'."
            )
        if out_path is None:
            out_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        cmd = [exe, "-v", self.voice, "-w", out_path]
        if self.rate:
            cmd += ["-s", str(self.rate)]
        cmd.append(text)
        subprocess.run(cmd, check=True, capture_output=True)
        if play:
            subprocess.run([exe, "-v", self.voice] + (["-s", str(self.rate)] if self.rate else []) + [text],
                           check=False, capture_output=True)
        return out_path

    def _speak_coqui(self, obj, out_path, *, use_ipa, dry_run) -> str:
        # Experimental: donor-language neural voice. Feeds surface text *with
        # punctuation* (neural TTS phrases on commas/periods); Coqui's own G2P
        # handles pronunciation. The IPA still rides on token._.ipa for a future
        # phoneme-level integration, but is not consumed by this backend.
        text = getattr(obj, "text", None) or " ".join(t.text for t in self._units(obj))
        if dry_run:
            return text
        try:
            from TTS.api import TTS  # type: ignore
        except ImportError as e:  # pragma: no cover - optional dep
            raise RuntimeError(
                "backend='coqui' needs the TTS package: `uv add TTS` (pulls torch). "
                "Or use the default backend='espeak-ng'."
            ) from e
        if self._coqui is None:  # pragma: no cover - heavy/optional
            self._coqui = TTS(self.coqui_model)
        if out_path is None:  # pragma: no cover
            out_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        self._coqui.tts_to_file(text=text, file_path=out_path)  # pragma: no cover
        return out_path

    # --- serialization (mirrors syllabifier) ------------------------------

    def _cfg(self) -> dict:
        return {
            "backend": self.backend, "voice": self.voice,
            "use_ipa": self.use_ipa, "rate": self.rate,
            "coqui_model": self.coqui_model,
            "lookup_path": str(self._lookup_path) if self._lookup_path else None,
        }

    def to_disk(self, path: str, *, exclude: tuple = ()) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        with open(p / "config.json", "w") as f:
            json.dump(self._cfg(), f)

    def from_disk(self, path: str, *, exclude: tuple = ()) -> "SpeakerComponent":
        cfg_file = Path(path) / "config.json"
        if cfg_file.exists():
            with open(cfg_file) as f:
                self._apply(json.load(f))
        return self

    def to_bytes(self, *, exclude: tuple = ()) -> bytes:
        return json.dumps(self._cfg()).encode("utf-8")

    def from_bytes(self, data: bytes, *, exclude: tuple = ()) -> "SpeakerComponent":
        if data:
            self._apply(json.loads(data.decode("utf-8")))
        return self

    def _apply(self, cfg: dict) -> None:
        self.backend = cfg.get("backend", "espeak-ng")
        self.voice = cfg.get("voice", "la")
        self.use_ipa = cfg.get("use_ipa", True)
        self.rate = cfg.get("rate", 120)
        self.coqui_model = cfg.get("coqui_model", "tts_models/es/css10/vits")
        if cfg.get("lookup_path"):
            self._lookup_path = cfg["lookup_path"]
            self._loaded = False
