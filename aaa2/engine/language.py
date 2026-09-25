"""Nyelvdetekció szövegből, magyar és angol között.

Nyelvre kizárólagos funkciószavak sűrűsége (találat / 1000 karakter). A két lista
metszete üres: ami mindkét nyelvben szó ("a", "is"), az egyikben sincs benne. A valódi
szöveg kb. 10 / 1000, a másik nyelvű kb. 0,5 / 1000; a küszöb 3,0. Az ékezetes betűk
számolása nem jó jel: angol szöveg is idézhet magyar tulajdonneveket.
"""
from __future__ import annotations

import re

HU_EXCLUSIVE = frozenset({
    "és", "egy", "az", "hogy", "nem", "vagy", "mely", "amely", "csak",
    "ami", "aki", "ahol", "amikor", "mivel", "hanem", "ezen", "ezért",
    "illetve", "valamint", "vagyis", "tehát", "azonban", "viszont",
    "alapján", "során", "között", "szerint", "miatt", "számára",
    "keresztül", "nélkül", "helyett", "után", "előtt", "alatt", "fölött",
    "mögött", "által", "lehet", "kell", "való", "már", "még", "így", "úgy",
    "ők", "ön", "weboldal", "oldal", "kereső", "webáruház", "című",
})
EN_EXCLUSIVE = frozenset({
    "the", "and", "that", "this", "with", "for", "are", "was", "were",
    "have", "has", "had", "will", "would", "should", "could", "their",
    "they", "them", "your", "our", "its", "which", "while", "when",
    "where", "what", "from", "into", "about", "through", "between",
    "because", "however", "therefore", "these", "those", "than", "then",
    "also", "such", "both", "each", "been", "being", "does", "here",
    "very", "more", "most", "some", "other", "only", "website", "page",
})
assert not HU_EXCLUSIVE & EN_EXCLUSIVE

DENSITY_THRESHOLD_PER_1000 = 3.0
_STOPLISTS = {"hu": HU_EXCLUSIVE, "en": EN_EXCLUSIVE}
_WORD = re.compile(r"\w+", re.UNICODE)


def density_per_1000(text: str, language: str) -> float:
    """A nyelv kizárólagos funkciószavainak találata 1000 karakterenként."""
    if not text:
        return 0.0
    stoplist = _STOPLISTS[language]
    hits = sum(1 for token in _WORD.findall(text.lower()) if token in stoplist)
    return hits * 1000.0 / len(text)


def detect_language(text: str) -> str | None:
    """'hu' vagy 'en', ha a sűrűbb nyelv eléri a küszöböt; különben None."""
    scores = {language: density_per_1000(text, language) for language in _STOPLISTS}
    best = max(scores, key=scores.get)
    return best if scores[best] >= DENSITY_THRESHOLD_PER_1000 else None
