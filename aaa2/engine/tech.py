"""Technológia a nyers tech-jelekből (`site.tech_signals`), szabály szerint, LLM nélkül.

A jel fajtái (`site_profile.page_tech_signals`): `generator:<meta generator>`, `script:<külső
script hosztja>`, `path:<ismert útvonal>`, `dom:<jelölő>`. A felismerés zárt táblákból megy:
ami nincs bennük, abból nem lesz technológia (a jel a nyers listában megmarad). A verzió a
generator- és a DOM-jelből jön, ha a jel megadja."""
from __future__ import annotations

import re
from collections.abc import Iterable

# a meta generator elejéről felismert rendszerek (a verzió az első számcsoport)
GENERATORS = ("WordPress", "WP Rocket", "WPML", "Drupal", "Joomla", "Shopify", "Wix",
              "Squarespace", "Webflow", "Astro", "Hugo", "Gatsby", "Next.js", "Ghost",
              "PrestaShop", "TYPO3", "WooCommerce")
# külső script hosztja (vagy a végződése) → szolgáltatás
SCRIPT_HOSTS = {
    "www.googletagmanager.com": "Google Tag Manager",
    "www.google-analytics.com": "Google Analytics",
    "googleads.g.doubleclick.net": "Google Ads",
    "www.clarity.ms": "Microsoft Clarity",
    "static.cloudflareinsights.com": "Cloudflare Web Analytics",
    "connect.facebook.net": "Meta Pixel",
    "chimpstatic.com": "Mailchimp",
    "form-assets.mailchimp.com": "Mailchimp",
    "pixel.barion.com": "Barion Pixel",
    "cdn-cookieyes.com": "CookieYes",
    "static.hotjar.com": "Hotjar",
    "js.hs-scripts.com": "HubSpot",
    "assets.pinterest.com": "Pinterest",
}
SCRIPT_SUFFIXES = {".cdn.shoprenter.hu": "Shoprenter", ".optimonk.com": "OptiMonk",
                   ".myshopify.com": "Shopify", ".cdn.unas.hu": "UNAS"}
PATHS = {"/wp-includes/": "WordPress", "/wp-json/": "WordPress", "/_astro/": "Astro",
         "/_next/": "Next.js", "/_nuxt/": "Nuxt"}
_WP_PART = re.compile(r"^/wp-content/(themes|plugins)/([^/]+)/$")
_VERSION = re.compile(r"\d+(?:\.\d+)*")
_DOM = {"ng-version": "Angular"}


def tech_from_signals(signals: Iterable[str] | None) -> list[str]:
    """A felismert technológiák a nyers jelekből, név szerint rendezve. A WordPress témája és
    bővítményei külön tételek („WordPress téma: Divi”, „WordPress bővítmény: contact-form-7”);
    a verzióval ismert rendszer a verziójával áll („WordPress 7.1.2”), a verzió nélküli alakja
    ilyenkor kimarad."""
    found: dict[str, str | None] = {}

    def add(name: str, version: str | None = None) -> None:
        if version or name not in found:
            found[name] = version or found.get(name)

    extras: set[str] = set()
    for signal in signals or ():
        kind, _, value = signal.partition(":")
        value = value.strip()
        if kind == "generator":
            for name in GENERATORS:
                if value.lower().startswith(name.lower()):
                    version = _VERSION.search(value[len(name):])
                    add(name, version.group(0) if version else None)
                    break
        elif kind == "script":
            host = value.lower()
            name = SCRIPT_HOSTS.get(host) or next(
                (label for suffix, label in SCRIPT_SUFFIXES.items() if host.endswith(suffix)),
                None)
            if name:
                add(name)
        elif kind == "path":
            part = _WP_PART.match(value)
            if part:
                add("WordPress")
                label = "téma" if part.group(1) == "themes" else "bővítmény"
                extras.add(f"WordPress {label}: {part.group(2)}")
            elif value in PATHS:
                add(PATHS[value])
        elif kind == "dom":
            key, _, version = value.partition("=")
            if key in _DOM:
                add(_DOM[key], version or None)
    names = [f"{name} {version}" if version else name for name, version in found.items()]
    return sorted([*names, *extras], key=str.lower)
