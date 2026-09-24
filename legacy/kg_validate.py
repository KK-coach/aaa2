"""AAA-56 Sub-step 2 — Google Knowledge Graph entity validation.

5-category structural classifier (NOT binary, NOT score-thresholded):
  high      name+type match + Wikipedia-backed detailedDescription
  medium    name+type match + description (no Wikipedia)
  stub      name+type match, no description/Wikipedia, exactly ONE such entity
  ambiguous name+type match, no description/Wikipedia, MULTIPLE same-name
  no_match  no name+type match in top results (junk only)

Recognition (high|medium) is a derived property, not stored.
resultScore is logged for audit only — it is cross-query incomparable
(Anthropic 10341 vs ABOUT YOU 2749 vs stub 24) and never decisional.
Name match = exact, case-insensitive, normalized (no fuzzy/partial) —
false-positive authority is costlier than false-negative for E-E-A-T.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import dotenv_values

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_LOG = logging.getLogger("aaa56.kg")
_ENDPOINT = "https://kgsearch.googleapis.com/v1/entities:search"
_API_VERSION = "kgsearch/v1"

# AAA-53 separation-of-concerns. audit_kg_cost_usd is ALWAYS 0.0 — Google KG
# Search API is free up to the daily quota. Track real usage via
# audit_kg_calls (the meaningful budget meter vs daily request quota).
_KG_USAGE = {"calls": 0, "cost_usd": 0.0}


def reset_kg_usage() -> None:
    _KG_USAGE["calls"] = 0
    _KG_USAGE["cost_usd"] = 0.0


def get_kg_usage() -> dict:
    return dict(_KG_USAGE)


def _norm(s: str) -> str:
    """lower + keep [a-z0-9 ] + collapse spaces. 'About You'->'about you',
    'kk.coach'->'kkcoach' (so it can never match KG 'Coach')."""
    s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def is_recognized(result: dict) -> bool:
    """Derived, not stored: only high|medium count as authority."""
    return (result or {}).get("category") in ("high", "medium")


def _empty(category: str, error: str | None = None) -> dict:
    return {
        "category": category,
        "result_score": None,
        "description": None,
        "detailed_description": None,
        "has_wikipedia": False,
        "kg_id": None,
        "types": [],
        "matched_entity_name": None,
        "alternative_count": 0,
        "_api_version": _API_VERSION,
        "_error": error,
    }


async def kg_lookup(entity_name: str, expected_types: list[str]) -> dict:
    """Look up entity_name in Google KG, restricted to expected_types.

    Never raises. On any API/transport/parse failure returns
    category='no_match' WITH _error set — callers (Sub-step 4) MUST treat
    `_error is not None` as 'skip finding' and NOT emit a 'not in KG'
    weakness (an API error is not evidence of absence).
    """
    _KG_USAGE["calls"] += 1
    name = (entity_name or "").strip()
    if not name:
        return _empty("no_match", "empty entity_name")

    # AAA-31 S3a: env-first (Cloud Run injects from Secret Manager via
    # --set-secrets), .env fallback for local dev. Same pattern as the S2
    # DataForSEO/OpenAI loaders.
    try:
        key = os.environ.get("GOOGLE_KG_API_KEY")
        if not key and _ENV_PATH.exists():
            key = dotenv_values(_ENV_PATH).get("GOOGLE_KG_API_KEY")
    except Exception as exc:  # noqa: BLE001
        return _empty("no_match", f"key load failed: {exc}")
    if not key:
        return _empty("no_match", "GOOGLE_KG_API_KEY missing from environment/.env")

    params = [
        ("query", name), ("key", key), ("limit", 10), ("languages", "en"),
    ] + [("types", t) for t in (expected_types or [])]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(_ENDPOINT, params=params)
    except httpx.TimeoutException:
        _LOG.warning("KG timeout entity=%r", name)
        return _empty("no_match", "timeout")
    except httpx.HTTPError as exc:
        _LOG.warning("KG transport error entity=%r: %s", name, exc)
        return _empty("no_match", f"transport: {exc!r}")

    try:
        body = resp.json()
    except ValueError:
        return _empty("no_match", f"non-JSON HTTP {resp.status_code}")

    if isinstance(body, dict) and body.get("error"):
        msg = body["error"].get("message", "unknown KG API error")
        # 4xx = misconfiguration, not transient -> no retry (per scope).
        _LOG.error("KG API error entity=%r: %s", name, msg[:160])
        return _empty("no_match", f"api_error: {msg[:160]}")

    items = (body or {}).get("itemListElement") or []
    want = {t.lower() for t in (expected_types or [])}
    norm_q = _norm(name)

    matches = []
    for it in items:
        res = it.get("result", {}) or {}
        rname = res.get("name") or ""
        rtypes = res.get("@type")
        rtypes = rtypes if isinstance(rtypes, list) else [rtypes]
        rtypes_l = {str(t).lower() for t in rtypes if t}
        if _norm(rname) == norm_q and (not want or (rtypes_l & want)):
            matches.append((it.get("resultScore"), it, res, rtypes))

    if not matches:
        _LOG.info("KG entity=%r types=%s -> no_match (alt=0)",
                  name, expected_types)
        return _empty("no_match")

    matches.sort(key=lambda m: m[0] or 0.0, reverse=True)
    alt = len(matches)
    score, _it, res, rtypes = matches[0]
    desc = res.get("description")
    dd = res.get("detailedDescription") or {}
    dd_body = dd.get("articleBody")

    if dd_body:
        category = "high"
    elif desc:
        category = "medium"
    else:
        category = "stub" if alt == 1 else "ambiguous"

    _LOG.info(
        "KG entity=%r types=%s -> %s (alt=%d score=%s matched=%r)",
        name, expected_types, category, alt, score, res.get("name"),
    )
    return {
        "category": category,
        "result_score": score,
        "description": desc,
        "detailed_description": dd_body,
        "has_wikipedia": bool(dd.get("url")),
        "kg_id": res.get("@id"),
        "types": [str(t) for t in rtypes if t],
        "matched_entity_name": res.get("name"),
        "alternative_count": alt,
        "_api_version": _API_VERSION,
        "_error": None,
    }
