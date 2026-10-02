"""Stabil JSON a tárolt és a kiírt értékekhez: a kulcsok rendezve, hogy ugyanaz az adat mindig
ugyanazt a szöveget adja (a bájtra azonos kimenet feltétele)."""
from __future__ import annotations

import json
from typing import Any


def dumps(value: Any, **options: Any) -> str:
    """`json.dumps` rendezett kulcsokkal; a listák sorrendje a hívóé."""
    return json.dumps(value, sort_keys=True, **options)
