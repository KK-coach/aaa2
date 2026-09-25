"""Country / TLD / phone / currency lookup tables (no API)."""

# Countries Gemini is allowed to return (must stay in sync with the prompt).
ALLOWED_LOCATIONS = [
    "Hungary", "Germany", "Austria", "Switzerland", "United Kingdom",
    "United States", "Canada", "Australia", "Ireland", "Italy", "France",
    "Spain", "Netherlands", "Belgium", "Czech Republic", "Poland",
    "Romania", "Croatia",
]

# Country-code TLDs -> country. Multi-part keys checked before single.
TLD_COUNTRY = {
    "co.uk": "United Kingdom", "org.uk": "United Kingdom",
    "com.au": "Australia", "net.au": "Australia",
    "hu": "Hungary", "de": "Germany", "at": "Austria", "ch": "Switzerland",
    "uk": "United Kingdom", "us": "United States", "ca": "Canada",
    "au": "Australia", "ie": "Ireland", "it": "Italy", "fr": "France",
    "es": "Spain", "nl": "Netherlands", "be": "Belgium", "cz": "Czech Republic",
    "pl": "Poland", "ro": "Romania", "hr": "Croatia",
}

# Generic / brandable TLDs carry NO location signal on their own.
# Stored as BARE labels (no leading dot) to match _tld_country()/_split_host
# which work on the last label. Rule: a TLD not in TLD_COUNTRY is treated as
# generic (English-global default) regardless of membership here; this set is
# the explicit allow-list + documentation.
GENERIC_TLDS = {
    # classic
    "com", "net", "org", "info", "biz",
    # modern tech
    "io", "co", "app", "dev", "tech", "cloud", "ai", "xyz",
    # industry-generic
    "coach", "agency", "studio", "consulting", "shop", "store",
    "online", "site",
    # other
    "edu", "museum", "travel",
}


def is_generic_tld(tld: str | None) -> bool:
    """Generic = NOT a known country-code TLD.

    Covers explicit generics, AND unknown/new TLDs (safety: default to
    generic so English-global default can apply rather than mis-mapping).
    """
    if not tld:
        return True
    bare = tld.lstrip(".")
    return bare not in TLD_COUNTRY

# ISO alpha-2 -> country name (only the allowed set).
COUNTRY_BY_CODE = {
    "HU": "Hungary", "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "GB": "United Kingdom", "UK": "United Kingdom", "US": "United States",
    "CA": "Canada", "AU": "Australia", "IE": "Ireland", "IT": "Italy",
    "FR": "France", "ES": "Spain", "NL": "Netherlands", "BE": "Belgium",
    "CZ": "Czech Republic", "PL": "Poland", "RO": "Romania", "HR": "Croatia",
}

# E.164 calling-code prefix -> country. "+1" is US/Canada ambiguous; we
# attribute it to United States but flag ambiguity in reasoning.
PHONE_PREFIX_COUNTRY = {
    "+36": "Hungary", "+49": "Germany", "+43": "Austria",
    "+41": "Switzerland", "+44": "United Kingdom", "+1": "United States",
    "+61": "Australia", "+353": "Ireland", "+39": "Italy", "+33": "France",
    "+34": "Spain", "+31": "Netherlands", "+32": "Belgium",
    "+420": "Czech Republic", "+48": "Poland", "+40": "Romania",
    "+385": "Croatia",
}

# Distinctive currency symbols — safe to match literally (no word collision).
CURRENCY_SYMBOLS = {
    "€": "EUR", "£": "GBP", "Kč": "CZK", "zł": "PLN", "Ft": "HUF",
}
# ISO 4217 codes — matched ONLY with word boundaries + case-sensitive, so
# "kn"/"lei"/"$" style fragments can't false-match inside English prose
# (this caused kk.coach -> Croatia via "kn" in "known"/"knowledge").
CURRENCY_ISO = {
    "HUF", "EUR", "GBP", "USD", "CHF", "CZK", "PLN", "RON", "HRK",
}

# Strong (unambiguous) currency -> country.
CURRENCY_COUNTRY = {
    "HUF": "Hungary", "GBP": "United Kingdom", "CHF": "Switzerland",
    "CZK": "Czech Republic", "PLN": "Poland", "RON": "Romania",
    "HRK": "Croatia",
}

# Second-level public suffixes used for domain-root extraction.
TWO_LABEL_SUFFIXES = {"co.uk", "org.uk", "com.au", "net.au", "co.nz"}
