"""AAA-130 Sub-step 1 — language-genuineness detector for summary fields.

Determines whether a piece of text is genuinely written in a target language
(hu/en), used to catch translate_summary failures: EN-verbatim passthrough and
near-English "cosmetic-edit" outputs that an exact-string check misses.

DETECTION METHOD: target-language-EXCLUSIVE function-word density (matches per
1000 chars) + an exact-verbatim fast-path. Measured separation (AAA-130 S0):
genuine target-language prose ~10 matches/1000 chars; failed (wrong-language)
output ~0.5/1000 — a ~20x gap. Threshold 3.0/1000 sits well inside it.

DEPRECATED — DO NOT USE raw-diacritic counting. Counting Hungarian accented
characters (á é í ó ö ő ú ü ű) to detect Hungarian FALSE-PASSES English text
that merely quotes Hungarian proper nouns / place names / product terms
(e.g. "...represents ABOUT YOU...", "...based in Csávoly, Hungary...",
'"mezőgazdasági gép alkatrészek"'). This trap produced wrong verdicts in
AAA-83 S0, S1, S2, and S2.5 repeatedly. Function-word density does not have
this failure mode because proper nouns are not function words.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Language-EXCLUSIVE function/stop word lists.
# Invariant: HU_EXCLUSIVE ∩ EN_EXCLUSIVE == ∅. Any token valid in BOTH
# languages (e.g. "is" = HU "also"/EN copula, "a" = HU "the"/EN article) is
# DROPPED — those would muddy the signal. Matched as whole words, lowercased,
# unicode-aware (\b around accented chars works under Python 3 re).
# ---------------------------------------------------------------------------
HU_EXCLUSIVE: frozenset[str] = frozenset({
    # high-frequency HU function words with no English-word collision
    "és", "egy", "az", "hogy", "nem", "vagy", "mely", "amely", "csak",
    "ami", "aki", "ahol", "amikor", "mivel", "hanem", "ezen", "ezért",
    "illetve", "valamint", "vagyis", "tehát", "azonban", "viszont",
    "alapján", "során", "között", "szerint", "miatt", "számára",
    "keresztül", "nélkül", "helyett", "után", "előtt", "alatt", "fölött",
    "mögött", "által", "lehet", "kell", "való", "már", "még", "így", "úgy",
    "ők", "ön", "weboldal", "oldal", "kereső", "webáruház", "című",
})
EN_EXCLUSIVE: frozenset[str] = frozenset({
    # high-frequency EN function words with no Hungarian-word collision
    # (deliberately excludes "a"/"is"/"to"/"in"/"on" — cross-lang ambiguous)
    "the", "and", "that", "this", "with", "for", "are", "was", "were",
    "have", "has", "had", "will", "would", "should", "could", "their",
    "they", "them", "your", "our", "its", "which", "while", "when",
    "where", "what", "from", "into", "about", "through", "between",
    "because", "however", "therefore", "these", "those", "than", "then",
    "also", "such", "both", "each", "been", "being", "does", "here",
    "very", "more", "most", "some", "other", "only", "website", "page",
})

# Defensive: enforce zero overlap at import time (catches future edits).
_overlap = HU_EXCLUSIVE & EN_EXCLUSIVE
if _overlap:  # pragma: no cover - guard
    raise AssertionError(f"HU/EN stoplists overlap: {_overlap}")

_STOPLISTS = {"hu": HU_EXCLUSIVE, "en": EN_EXCLUSIVE}

DENSITY_THRESHOLD_PER_1000 = 3.0  # genuine ~10/1000, fail ~0.5/1000 (S0)

# Unicode-aware word tokenizer (keeps accented HU letters as word chars).
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _density_per_1000(text: str, target_lang: str) -> float:
    """Target-language-exclusive function-word matches per 1000 chars."""
    stop = _STOPLISTS[target_lang]
    n_chars = len(text)
    if n_chars == 0:
        return 0.0
    tokens = _WORD_RE.findall(text.lower())
    hits = sum(1 for t in tokens if t in stop)
    return round(hits * 1000.0 / n_chars, 3)


def is_genuine_target_language(
    text: str,
    target_lang: str,
    source_text: str | None = None,
) -> dict:
    """Is `text` genuinely written in `target_lang` ('hu' | 'en')?

    Returns {is_genuine: bool, density: float, reason: str}.
      reason ∈ {"verbatim", "low_density", "ok", "empty", "unsupported_lang"}.

    - verbatim fast-path: if source_text given and text == source_text
      (stripped) -> passthrough, not genuine.
    - density check: target-exclusive function words / 1000 chars must meet
      DENSITY_THRESHOLD_PER_1000.
    - unsupported target_lang -> is_genuine True (cannot assess; never
      false-flag), reason "unsupported_lang".
    """
    tl = (target_lang or "").lower()
    if tl not in _STOPLISTS:
        return {"is_genuine": True, "density": 0.0, "reason": "unsupported_lang"}

    t = (text or "").strip()
    if not t:
        return {"is_genuine": False, "density": 0.0, "reason": "empty"}

    density = _density_per_1000(t, tl)

    if source_text is not None and t == (source_text or "").strip():
        return {"is_genuine": False, "density": density, "reason": "verbatim"}

    if density < DENSITY_THRESHOLD_PER_1000:
        return {"is_genuine": False, "density": density, "reason": "low_density"}

    return {"is_genuine": True, "density": density, "reason": "ok"}
