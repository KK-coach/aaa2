"""Coordination + confidence fusion for site-level profiling."""

from __future__ import annotations

from site_profile.constants import (
    ALLOWED_LOCATIONS,
    CURRENCY_COUNTRY,
    is_generic_tld,
)
from site_profile.gemini_analyzer import gemini_site_analysis
from site_profile.consistency import build_signal_consistency
from site_profile.heuristics import extract_heuristic_signals


def _primary_lang(code: str | None) -> str | None:
    if not code:
        return None
    return code.strip().lower().replace("_", "-").split("-")[0] or None


def _fuse_language(heur: dict, gem: dict) -> tuple[str | None, float]:
    lh = heur["language_hints"]
    heur_lang = _primary_lang(
        lh["html_lang"]
        or lh["content_language_meta"]
        or (lh["hreflang_languages"][0] if lh["hreflang_languages"] else None)
    )
    gem_lang = _primary_lang(gem.get("language")) if gem.get("available") else None

    if heur_lang and gem_lang:
        if heur_lang == gem_lang:
            return heur_lang, 0.95
        return gem_lang, 0.50  # conflict — trust content over markup, low conf
    if gem_lang:
        return gem_lang, 0.75
    if heur_lang:
        return heur_lang, 0.65  # heuristic-only (e.g. Gemini unavailable)
    return None, 0.0


def _location_scores(
    heur: dict, gem: dict, og_locale_isolated: bool = False
) -> dict[str, float]:
    """Weighted votes per country from every available signal.

    If og:locale is isolated (no corroborating signal), its weight is halved
    (2.0 -> 1.0) so an orphan Yoast-style en_GB acts as a soft hint, not
    strong evidence.
    """
    loc = heur["location_hints"]
    scores: dict[str, float] = {}

    def add(country: str | None, weight: float) -> None:
        if country and country in ALLOWED_LOCATIONS:
            scores[country] = scores.get(country, 0.0) + weight

    add(loc["tld_country"], 3.0)               # ccTLD = strong
    for c in loc["hreflang_countries"]:
        add(c, 1.0)
    add(loc["path_prefix_country"], 1.5)
    for p in loc["phone_prefixes"]:
        add(p["country"], 2.0)                  # impressum phone = strong
    add(loc["schema_address_country"], 2.0)
    add(loc.get("og_locale_country"), 1.0 if og_locale_isolated else 2.0)
    for cur in loc["currencies_found"]:
        add(CURRENCY_COUNTRY.get(cur), 1.0)     # only unambiguous currencies
    if gem.get("available") and gem.get("location") not in (None, "AMBIGUOUS"):
        add(gem.get("location"), 3.0)           # Gemini content read = strong
    return scores


def _fuse_location(heur: dict, gem: dict, og_locale_isolated: bool = False):
    loc = heur["location_hints"]
    scores = _location_scores(heur, gem, og_locale_isolated)
    gem_ok = gem.get("available", False)
    gem_target = gem.get("location") if gem_ok else None
    tld_country = loc["tld_country"]

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(scores.values()) or 1.0
    candidates = [
        {
            "location": c,
            "score": round(s / total, 2),
            "reasoning": f"weighted signal score {s:.1f}",
        }
        for c, s in ranked[:3]
    ]

    # --- confidence bands (per spec) ---
    # Only fall straight to ambiguous if Gemini is unsure AND no heuristic
    # produced a country (an explicit og:locale / phone / schema still wins).
    if (
        gem_ok
        and gem_target == "AMBIGUOUS"
        and not tld_country
        and not scores
    ):
        return None, 0.20, candidates, "Gemini could not determine a target."

    top = ranked[0][0] if ranked else None

    # Distinct strong signals agreeing on `top`.
    strong = set()
    if tld_country == top:
        strong.add("tld")
    if top in loc["hreflang_countries"]:
        strong.add("hreflang")
    if any(p["country"] == top for p in loc["phone_prefixes"]):
        strong.add("phone")
    if loc["schema_address_country"] == top:
        strong.add("schema")
    if (
        not og_locale_isolated
        and loc.get("og_locale_country") == top
        and top is not None
    ):
        strong.add("og_locale")  # corroborated declaration -> strong
    if gem_ok and gem_target == top:
        strong.add("gemini")

    heur_top = None
    heur_only_scores = _location_scores(
        heur, {"available": False}, og_locale_isolated
    )
    if heur_only_scores:
        heur_top = max(heur_only_scores, key=heur_only_scores.get)

    conflict = gem_ok and heur_top and gem_target not in (
        None, "AMBIGUOUS", heur_top,
    )

    if tld_country and gem_ok and gem_target == tld_country:
        conf = 0.95
    elif len(strong) >= 3:
        conf = 0.85
    elif conflict:
        # heuristics vs Gemini disagree — low, scaled by how strong each is
        conf = 0.40
        top = gem_target or heur_top  # report Gemini's read but flag it
    elif len(strong) == 2:
        conf = 0.70
    elif len(strong) == 1:
        conf = 0.55
    else:
        conf = 0.30

    if not gem_ok:
        conf = min(conf, 0.70)  # cannot reach high bands without Gemini

    # An explicit, author-declared og:locale country is authoritative when
    # nothing contradicts it - treat as a confident country_specific result.
    if "og_locale" in strong and not conflict:
        conf = max(conf, 0.85)

    return top, round(conf, 2), candidates, None


def _fuse_brand(heur: dict, gem: dict) -> tuple[str, str]:
    """Prefer schema/og site name; upgrade to Gemini's legal name when the
    schema name is just the domain; fall back to the domain root."""
    bh = heur["brand_hints"]
    site_name = bh["site_name"]
    domain_root = bh["domain_root"]
    gem_brand = gem.get("brand") if gem.get("available") else None

    if site_name:
        if (
            gem_brand
            and site_name.lower().replace(" ", "") == domain_root.lower()
            and len(gem_brand) > len(site_name)
        ):
            return gem_brand, "gemini (schema name was just the domain)"
        return site_name, "schema.org / og:site_name"
    if gem_brand:
        return gem_brand, "gemini (no schema/og name)"
    return domain_root.capitalize(), "domain root (fallback)"


async def analyze_site_profile(crawl_result: dict) -> dict:
    """Determine site-level profile: locale + brand + entities + industry."""
    if crawl_result.get("error"):
        return {
            "error": f"crawl failed: {crawl_result.get('error')}",
            "error_type": crawl_result.get("error_type"),
        }

    heur = extract_heuristic_signals(crawl_result)
    gem = await gemini_site_analysis(crawl_result, heur)
    loc = heur["location_hints"]

    # Cross-signal consistency: an isolated (orphan) og:locale is downgraded
    # to a soft hint and reported as an actionable finding.
    signal_consistency, og_locale_isolated = build_signal_consistency(loc, gem)

    language, lang_conf = _fuse_language(heur, gem)
    location, loc_conf, candidates, amb_reason = _fuse_location(
        heur, gem, og_locale_isolated
    )
    brand, brand_src = _fuse_brand(heur, gem)

    gem_ok = gem.get("available", False)

    # --- "English defaults to global/US" rule + serp_strategy ---
    tld_generic = is_generic_tld(loc["tld"])
    schema_c = loc["schema_address_country"]
    strong_country_signals = [
        None if og_locale_isolated else loc.get("og_locale_country"),
        loc["hreflang_countries"] or None,
        schema_c if schema_c in ALLOWED_LOCATIONS else None,
        loc["phone_prefixes"] or None,
        (gem.get("location")
         if gem_ok and gem.get("location") not in (None, "AMBIGUOUS")
         else None),
        (None if tld_generic else loc["tld_country"]),
    ]
    no_strong_country_signal = not any(strong_country_signals)

    serp_strategy: str
    english_default_applied = False
    if language == "en" and tld_generic and no_strong_country_signal:
        # Generic TLD + English + zero country signals -> global English
        # SERP, which DataForSEO models as United States (google.com).
        location = "United States"
        loc_conf = 0.65
        serp_strategy = "english_global_default"
        english_default_applied = True
    elif location is not None and (
        loc_conf >= 0.85 or (not tld_generic and loc_conf >= 0.55)
    ):
        serp_strategy = "country_specific"
    else:
        serp_strategy = "needs_user_confirmation"

    needs_confirmation = serp_strategy == "needs_user_confirmation"

    reasoning_bits = []
    for c in signal_consistency["conflicts"]:
        reasoning_bits.append(
            f"SIGNAL CONFLICT [{c['severity']}]: {c['field']} declares "
            f"'{c['declared']}' but no other signal supports it - downgraded "
            f"to a soft hint."
        )
    if english_default_applied:
        reasoning_bits.append(
            f"English-global default: '{loc['tld']}' is a generic TLD, "
            f"language=en, and no country-specific signal "
            f"(og:locale/hreflang/schema/phone/Gemini) -> United States "
            f"(global English SERP)."
        )
    if amb_reason:
        reasoning_bits.append(amb_reason)
    reasoning_bits.append(
        f"TLD {loc['tld'] or 'n/a'} -> {loc['tld_country'] or 'generic'}; "
        f"phone {[p['prefix'] for p in loc['phone_prefixes']] or 'none'}; "
        f"schema country {loc['schema_address_country'] or 'none'}."
    )
    if gem_ok:
        reasoning_bits.append(
            f"Gemini target={gem.get('location')} "
            f"(self {gem.get('confidence_self_assessment')}). "
            f"{gem.get('reasoning', '')}"
        )
    else:
        reasoning_bits.append(
            f"Gemini UNAVAILABLE ({gem.get('error')}) - heuristics only, "
            f"confidence capped."
        )
    reasoning_bits.append(f"Brand from {brand_src}.")

    return {
        "_model_version": gem.get("_model_version"),
        "language": language,
        "language_confidence": lang_conf,
        "location": location,
        "location_confidence": loc_conf,
        "needs_user_confirmation": needs_confirmation,
        "serp_strategy": serp_strategy,
        "signal_consistency": signal_consistency,
        "brand": brand,
        "industry": gem.get("industry") if gem_ok else None,
        "audience": gem.get("audience") if gem_ok else None,
        "main_entities": gem.get("main_entities", []) if gem_ok else [],
        "detected_signals": {
            "tld": loc["tld"],
            "hreflang_values": [
                e.get("hreflang")
                for e in (crawl_result.get("i18n") or {}).get("hreflang", [])
            ],
            "address_country": loc["schema_address_country"],
            "og_locale": loc.get("og_locale"),
            "og_locale_country": loc.get("og_locale_country"),
            "phone_prefix": (
                loc["phone_prefixes"][0]["prefix"]
                if loc["phone_prefixes"]
                else None
            ),
            "currency": (
                loc["currencies_found"][0] if loc["currencies_found"] else None
            ),
            "gemini_target": gem.get("location") if gem_ok else None,
            "gemini_self_assessment": (
                gem.get("confidence_self_assessment") if gem_ok else None
            ),
        },
        "candidates": candidates,
        "reasoning": " ".join(b for b in reasoning_bits if b).strip(),
    }
