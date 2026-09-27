"""A bemeneti teljesség mérése (tests/acceptance/input_completeness.py), hálózat és adatbázis
nélkül."""
import pytest

import tests.acceptance.input_completeness as ic
from aaa2.engine.parse import extract_main_content

WORDS = " ".join(f"szó{i}" for i in range(120))          # a main content küszöbe fölött

PAGE = f"""<html><body>
<a href="#main">Ugrás a tartalomra</a>
<header><nav><ul><li>Menü egy</li><li>Menü kettő</li></ul></nav></header>
<main>
  <h1>Cím</h1>
  <p>Bevezető <b>félkövér</b> szöveg {WORDS}</p>
  <ul><li>Első tétel <a href="#">linkkel</a></li><li>Második tétel</li></ul>
  <table><tr><th>Név</th><th>Érték</th></tr><tr><td>Selector</td><td><code></code></td></tr>
    <tr><td>isAnimated</td><td><div>boolean</div></td></tr></table>
  <pre class="prettyprint"><ol><li>const a = 1;</li><li>const b = 2;</li></ol></pre>
  <div class="cards">
    <div class="card"><h3>Mérés</h3><div>Rövid leírás egy</div></div>
    <div class="card"><h3>SEO</h3><div>Rövid leírás kettő</div></div>
  </div>
  <div hidden>Rejtett szöveg</div>
  <div class="cky-modal"><div role="dialog" aria-modal="true"><p>Sütibeállítás</p></div></div>
  <footer><p>Lábléc a mainben</p></footer>
</main>
<div class="site-footer"><p>Lábléc-widget</p></div>
<div class="thanks"><p>Foglalás megerősítve</p></div>
</body></html>"""


def blocks_by_text():
    blocks, method, text = ic.dom_blocks(PAGE)
    return {b.text: b for b in blocks}, method, text


def test_block_kinds_and_texts():
    blocks, _, _ = blocks_by_text()
    assert blocks["Cím"].kind == "heading"
    assert blocks[f"Bevezető félkövér szöveg {WORDS}"].kind == "paragraph"
    assert blocks["Első tétel linkkel"].kind == "list_item"
    assert blocks["Név | Érték"].kind == "table_row"
    assert "Selector" in blocks and blocks["Selector"].kind == "table_row"   # az üres cella nélkül
    assert blocks["isAnimated | boolean"].kind == "table_row"
    assert blocks["const a = 1; const b = 2;"].kind == "code"                # egy <pre>, egy blokk
    assert (blocks["Mérés"].kind, blocks["Rövid leírás egy"].kind) == ("heading", "card")


def test_hidden_dialog_and_chrome():
    blocks, _, _ = blocks_by_text()
    assert "Rejtett szöveg" not in blocks and "Sütibeállítás" not in blocks
    assert blocks["Menü egy"].region == "chrome"
    assert blocks["Lábléc a mainben"].region == "chrome"
    assert blocks["Lábléc-widget"].region == "chrome"                        # site-footer osztály
    assert blocks["Foglalás megerősítve"].region == "content"


def test_root_marks_what_reaches_main_content():
    blocks, method, text = blocks_by_text()
    assert method == "semantic_main"
    assert blocks["Cím"].in_root and blocks["Lábléc a mainben"].in_root     # chrome a gyökérben
    assert not blocks["Foglalás megerősítve"].in_root                       # kimarad
    assert not blocks["Ugrás a tartalomra"].in_root
    assert text == extract_main_content(PAGE)[0]


@pytest.mark.parametrize("html", [
    PAGE,
    f"<html><body><article><p>{WORDS}</p></article><p>kívül</p></body></html>",
    f"<html><body><div id='content'><p>{WORDS}</p></div><footer>láb</footer></body></html>",
    "<html><body><header>fej</header><p>rövid oldal</p><footer>láb</footer></body></html>",
])
def test_root_text_equals_the_extractor(html):
    _, method, text = ic.dom_blocks(html)
    assert (text, method) == extract_main_content(html)


def test_fallback_body_leaves_out_chrome():
    html = "<html><body><header>fej</header><p>rövid oldal</p><footer>láb</footer></body></html>"
    blocks, method, text = ic.dom_blocks(html)
    by_text = {b.text: b for b in blocks}
    assert (method, text) == ("fallback_body", "rövid oldal")
    assert by_text["rövid oldal"].in_root and not by_text["láb"].in_root


def test_report_counts_and_hides_locked_text():
    report = ic.page_report("kk-coach-crawl", "https://x.test/", PAGE,
                            extract_main_content(PAGE)[0], "semantic_main", locked=False)
    assert report.extractor_matches
    assert [b.text for b in report.missing] == ["Ugrás a tartalomra", "Foglalás megerősítve"]
    hit, total = report.coverage()
    assert total - hit == 5
    text = "\n".join(ic.page_markdown(report))
    assert "Foglalás megerősítve" in text
    locked = ic.page_report("kk-coach-crawl", "https://x.test/", PAGE,
                            extract_main_content(PAGE)[0], "semantic_main", locked=True)
    text = "\n".join(ic.page_markdown(locked))
    assert "Foglalás megerősítve" not in text
    assert "zárolt tesztoldal: 2 kimaradt blokk, 5 szó (szöveg nélkül)" in text


def test_truncation_is_counted_from_the_input_limit():
    long = "x" * (ic.MAX_INPUT_CHARS + 10)
    report = ic.page_report("s", "u", PAGE, long, "semantic_main", locked=False)
    assert report.truncated_chars == 10 and not report.extractor_matches
