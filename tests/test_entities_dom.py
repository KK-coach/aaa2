"""A blokkmodell (aaa2/entities/dom.py): blokkok a renderelt DOM-ból, típussal, régióval,
heading-útvonallal, táblázatsornál a cellákkal; és a `blocks` tábla."""
from aaa2.entities.dom import build_blocks, page_blocks, parse_blocks
from tests.test_entities_rules import html, site

PAGE = """
<a href="#main">Ugrás a tartalomra</a>
<header><nav><ul><li><a href="/meres/">Mérés</a></li></ul></nav></header>
<main>
  <h1>Szolgáltatások</h1>
  <p>Bevezető a <a href="/ga4/">GA4</a> és a <b>BigQuery</b> világába.</p>
  <h2>Csomagok</h2>
  <h3>Alap</h3>
  <ul><li>Első tétel<ul><li>Beágyazott</li></ul></li></ul>
  <table>
    <thead><tr><th>Csomag</th><th>Ár</th><th>Megjegyzés</th></tr></thead>
    <tbody><tr><td>Starter</td><td>29 €</td><td><code></code></td></tr>
      <tr><td><div>Growth</div></td><td>59 €</td><td>népszerű</td></tr></tbody>
  </table>
  <h2>Kód</h2>
  <pre class="prettyprint"><ol><li>npm install ngx-bootstrap</li><li>ng serve</li></ol></pre>
  <pre>import x
    from y</pre>
  <div class="cards">
    <div class="card"><h3>Audit</h3><p>Rövid egy</p></div>
    <div class="card"><h3>SEO</h3><p>Rövid kettő</p></div>
  </div>
  <div hidden>Rejtett</div>
  <div role="dialog" aria-modal="true"><p>Sütibeállítások</p></div>
  <div style="display: none">Inline rejtett</div>
  <script>var titok = 1;</script>
</main>
<div class="site-footer"><p>Lábléc</p></div>
"""


def blocks():
    return parse_blocks(f"<html><head><title>T</title></head><body>{PAGE}</body></html>",
                        "Szolgáltatások | Példa")


def by_text():
    return {b.text: b for b in blocks()}


def test_title_is_block_zero_and_the_rest_follow_in_document_order():
    parsed = blocks()
    assert (parsed[0].ordinal, parsed[0].kind, parsed[0].text) == (0, "title",
                                                                    "Szolgáltatások | Példa")
    assert [b.ordinal for b in parsed] == list(range(len(parsed)))
    assert [b.text for b in parsed][1:4] == ["Ugrás a tartalomra", "Mérés", "Szolgáltatások"]


def test_kinds_levels_and_heading_paths():
    found = by_text()
    assert (found["Szolgáltatások"].kind, found["Szolgáltatások"].level) == ("heading", 1)
    assert found["Alap"].heading_path == ["Szolgáltatások", "Csomagok", "Alap"]
    assert found["Kód"].heading_path == ["Szolgáltatások", "Kód"]            # a h3 lezárul
    assert found["Bevezető a GA4 és a BigQuery világába."].kind == "paragraph"
    assert found["Bevezető a GA4 és a BigQuery világába."].heading_path == ["Szolgáltatások"]
    assert (found["Első tétel"].kind, found["Beágyazott"].kind) == ("list_item", "list_item")


def test_table_rows_carry_their_cells_with_the_column_headers():
    found = by_text()
    assert found["Csomag | Ár | Megjegyzés"].cells == [
        {"header": None, "value": v} for v in ("Csomag", "Ár", "Megjegyzés")]
    assert found["Starter | 29 €"].cells == [                    # az üres cella kimarad
        {"header": "Csomag", "value": "Starter"}, {"header": "Ár", "value": "29 €"}]
    assert found["Growth | 59 € | népszerű"].cells[0] == {"header": "Csomag", "value": "Growth"}
    assert found["Growth | 59 € | népszerű"].kind == "table_row"


def test_code_keeps_its_lines():
    found = by_text()
    assert found["npm install ngx-bootstrap\nng serve"].kind == "code"
    assert found["import x\n    from y"].kind == "code"


def test_cards_regions_and_hidden_elements():
    found = by_text()
    assert (found["Mérés"].region, found["Mérés"].kind) == ("chrome", "list_item")
    assert (found["Rövid egy"].region, found["Rövid egy"].kind) == ("content", "card")
    assert (found["Audit"].kind, found["SEO"].kind) == ("heading", "heading")
    assert found["Lábléc"].region == "chrome"
    assert found["Ugrás a tartalomra"].region == "content"
    for gone in ("Rejtett", "Sütibeállítások", "Inline rejtett", "var titok = 1;"):
        assert gone not in found


def test_anchors_are_listed_per_block():
    found = by_text()
    assert found["Bevezető a GA4 és a BigQuery világába."].anchors == ["GA4"]
    assert found["Mérés"].anchors == ["Mérés"]


def test_build_blocks_once_and_read_them_back():
    con = site({"/": html("Példa | Kávé", "<h1>Példa</h1><p>Szöveg</p><footer>Láb</footer>"),
                "/b/": html("B", "<p>Más</p>")})
    assert build_blocks(con) == 2
    first = con.execute("SELECT * FROM blocks ORDER BY block_id").fetchall()
    assert build_blocks(con) == 0
    assert con.execute("SELECT * FROM blocks ORDER BY block_id").fetchall() == first
    content = page_blocks(con, 1, region="content")
    assert [(b["id"], b["kind"], b["text"]) for b in content] == [
        ("b0", "title", "Példa | Kávé"), ("b1", "heading", "Példa"), ("b2", "paragraph", "Szöveg")]
    assert [b["region"] for b in page_blocks(con, 1)] == ["content"] * 3 + ["chrome"]
