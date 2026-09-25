"""Stabil hash a nyers HTML-re: sha256 a kérésenként változó tokenek nélkül.

A tokenmintákat a `config/volatile_patterns.txt` sorolja fel (egy sor = egy regex, a
találat kimarad). Ugyanez adja a `RenderResult.raw_html_hash`-t és a crawl hash-próbáját,
így a két oldal ugyanazt hasonlítja.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

PATTERNS_FILE = Path(__file__).parent / "config" / "volatile_patterns.txt"


@lru_cache(maxsize=1)
def volatile_patterns() -> tuple[re.Pattern[str], ...]:
    """A mintafájl regexei; a '#'-os és az üres sorok kimaradnak."""
    lines = PATTERNS_FILE.read_text(encoding="utf-8").splitlines()
    return tuple(
        re.compile(line, re.IGNORECASE)
        for line in lines
        if line.strip() and not line.startswith("#")
    )


def strip_volatile(html: str) -> str:
    """A HTML a kérésenként változó tokenek nélkül."""
    for pattern in volatile_patterns():
        html = pattern.sub("", html)
    return html


def stable_hash(raw_html: str) -> str:
    """sha256 a nyers HTML-re, a változó tokenek nélkül."""
    return hashlib.sha256(strip_volatile(raw_html).encode("utf-8")).hexdigest()


def decode_raw(raw: bytes) -> str:
    """A nyers válasz szövegként, mindkét oldalon ugyanígy dekódolva."""
    return raw.decode("utf-8", "replace")
