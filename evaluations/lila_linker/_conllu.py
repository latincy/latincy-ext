"""Minimal CoNLL-U / CoNLL-U-Plus reading shared by the eval scripts.

Self-contained on purpose: the eval folder must run from a checkout of
latincy-ext + a linkbank artifact, without the latincy-treebanks build tree.
"""

from __future__ import annotations

from typing import Iterator, List, Optional, Tuple

_LEMMA_PREFIX = "http://lila-erc.eu/data/id/lemma/"
_HYPO_PREFIX = "http://lila-erc.eu/data/id/hypolemma/"


def uri_from_field(f: str) -> Tuple[Optional[str], Optional[str]]:
    """(uri, kind) from a single column, across the format variants LiLa emits."""
    if f.startswith("http") and "lila-erc.eu" in f:
        if "/lemma/" in f:
            return f, "lemma"
        if "/hypolemma/" in f:
            return f, "hypolemma"
    if f.startswith("lilaLemma:"):
        return _LEMMA_PREFIX + f.split(":", 1)[1], "lemma"
    if f.startswith(("lilaIpoLemma:", "lilaIpolemma:", "lilaHypoLemma:")):
        return _HYPO_PREFIX + f.split(":", 1)[1], "hypolemma"
    return None, None


def token_uri(cols: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Gold URI attached to a token row (searched from the FEATS column on)."""
    for f in cols[3:]:
        uri, kind = uri_from_field(f)
        if uri:
            return uri, kind
    return None, None


def text_lines(path: str) -> List[str]:
    """Raw sentence strings from ``# text =`` comments."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("# text ="):
                out.append(line.split("=", 1)[1].strip())
    return out


def token_rows(path: str) -> Iterator[List[str]]:
    """Yield real token rows (skip comments, blanks, multiword/empty nodes)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4 or "-" in cols[0] or "." in cols[0]:
                continue
            yield cols


def sentences(path: str) -> Iterator[List[List[str]]]:
    """Yield sentences as lists of token-row column lists."""
    sent: List[List[str]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                if sent:
                    yield sent
                    sent = []
                continue
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4 or "-" in cols[0] or "." in cols[0]:
                continue
            sent.append(cols)
    if sent:
        yield sent
