"""Rögzített site-ok visszajátszva, hálózat nélkül. A felvétel a `live` jelölésű tesztekkel
készül (`pytest -m live -k <név>`), a visszajátszás kimarad, ha nincs felvétel.

Victoria Moda (MicroStore): negatív eset. A render sikeres, a JS lefut, de a DOM-ban
nincs `<a href>`: a site áll, de nincs mit crawlolni.
"""
from urllib.parse import urljoin

import pytest
from selectolax.parser import HTMLParser

from aaa2.engine.normalize import UrlPolicy, is_internal, normalize
from aaa2.engine.render import Renderer
from tests.recorded import Recording

VICTORIA = "https://victoria.microstore.app/"


def internal_links(html, base, policy):
    found = set()
    for a in HTMLParser(html or "").css("a[href]"):
        url = urljoin(base, a.attributes.get("href") or "")
        if is_internal(url, policy) and (target := normalize(url, policy)):
            found.add(target)
    return found


def words(html):
    tree = HTMLParser(html or "")
    for node in tree.css("script,style,noscript,template"):
        node.decompose()
    return len((tree.body.text(separator=" ") if tree.body else "").split())


def measure(result, seed):
    return {
        "status": result.status,
        "error": result.error,
        "internal_links": len(internal_links(result.rendered_html, result.final_url or seed,
                                             UrlPolicy.from_seed(seed))),
        "rendered_words": words(result.rendered_html),
        "raw_words": words(result.raw_html.decode("utf-8", "replace") if result.raw_html else ""),
    }


@pytest.mark.live
async def test_live_record_victoria():
    recording = Recording("victoria.microstore.app")
    recording.clear()
    async with Renderer(concurrency=1, upstream=recording.record) as renderer:
        result = await renderer.render(VICTORIA)
    measured = measure(result, VICTORIA)
    recording.save(**measured, final_url=result.final_url)
    print(f"\nvictoria felvétel: {measured} válasz={len(recording.responses)}")
    assert (measured["status"], measured["error"], measured["internal_links"]) == (200, None, 0)
    assert measured["rendered_words"] > measured["raw_words"]


async def test_victoria_renders_without_links():
    recording = Recording("victoria.microstore.app")
    if not recording.exists:
        pytest.skip("nincs felvétel: pytest -m live -k victoria")
    async with Renderer(concurrency=1, upstream=recording.replay) as renderer:
        result = await renderer.render(VICTORIA)
    measured = measure(result, VICTORIA)
    assert (measured["status"], measured["error"], measured["internal_links"]) == (200, None, 0)
    assert measured["rendered_words"] == recording.measured["rendered_words"]
    assert measured["rendered_words"] > measured["raw_words"]
