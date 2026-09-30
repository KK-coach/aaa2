"""A blokkmodell (aaa2/entities/dom.py): blokkok a renderelt DOM-ból, típussal, régióval,
heading-útvonallal, táblázatsornál a cellákkal; és a `blocks` tábla."""
from aaa2.entities.blocks import CHUNK_MAX_BLOCKS, chunk_blocks
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


def texts(body):
    return [b.text for b in parse_blocks(f"<html><body>{body}</body></html>")]


def test_sibling_inline_labels_are_separated():
    assert texts("<div><a href='/a'>GA4</a><a href='/b'>GTM</a><span>Stape.io</span></div>") == [
        "GA4 · GTM · Stape.io"]
    # saját szöveggel, headingben és bekezdésben nem címkelista
    assert texts("<div>Lásd: <a href='/a'>GA4</a> <b>GTM</b></div>") == ["Lásd: GA4 GTM"]
    assert texts("<h2><span>Mérés</span> <span>és adat</span></h2>") == ["Mérés és adat"]
    assert texts("<p><a href='/a'>GA4</a> <a href='/b'>GTM</a></p>") == ["GA4 GTM"]
    assert texts("<div><a href='/a'>GA4</a><br><img alt='x'></div>") == ["GA4"]


GRID = """
<h2>Árazás</h2>
<div class="grid">
  <div class="c1"><p>Project</p></div><div class="c2"><p>Est. hours</p></div>
  <div class="c3"><p>Est. cost</p></div>
  <div class="c4"><p><strong>Szerver oldali mérés</strong></p><p>GTM, GA4 és CAPI</p></div>
  <div class="c5"><p>8-14 h</p></div><div class="c6"><p>108000–184000 Ft</p></div>
  <div class="c7"><p><strong>BigQuery integráció</strong></p><p>GA4 export</p></div>
  <div class="c8"><p>10-18 h</p></div><div class="c9"><p>160000-208000 Ft</p></div>
</div>
<p>Óradíj</p>
"""


def test_a_flat_div_grid_with_a_header_row_becomes_table_rows():
    parsed = parse_blocks(f"<html><body>{GRID}</body></html>")
    assert [(b.kind, b.text) for b in parsed] == [
        ("heading", "Árazás"),
        ("table_row", "Project | Est. hours | Est. cost"),
        ("table_row", "Szerver oldali mérés · GTM, GA4 és CAPI | 8-14 h | 108000–184000 Ft"),
        ("table_row", "BigQuery integráció · GA4 export | 10-18 h | 160000-208000 Ft"),
        ("paragraph", "Óradíj")]
    assert parsed[2].cells == [
        {"header": "Project", "value": "Szerver oldali mérés · GTM, GA4 és CAPI"},
        {"header": "Est. hours", "value": "8-14 h"},
        {"header": "Est. cost", "value": "108000–184000 Ft"}]
    assert parsed[1].cells[0] == {"header": None, "value": "Project"}


def test_not_a_grid():
    # egy adatsor: kevés sor
    assert all(b.kind == "paragraph" for b in parse_blocks(
        "<html><body><div><div><p>A</p></div><div><p>B</p></div><div><p>x</p></div>"
        "<div><p>1 h</p></div></div></body></html>"))
    # nincs számos oszlop (kártyasor)
    cards = "".join(f"<div><p>Cím {c}</p></div><div><p>Leírás {c}</p></div>" for c in "abc")
    assert all(b.kind == "paragraph" for b in parse_blocks(f"<html><body><div>{cards}</div>"
                                                            "</body></html>"))
    # a cellában lista van
    listed = GRID.replace("<p>GA4 export</p>", "<ul><li>GA4 export</li></ul>")
    assert "table_row" not in {b.kind for b in parse_blocks(f"<html><body>{listed}</body>"
                                                            "</html>")}


TABS = """
<h1>Oldal</h1>
<div class="tab-content">
  <div class="tab-pane active"><h2>Példa</h2><div class="card">Static Header</div></div>
  <div class="tab-pane"><h2>API</h2><p>closeOthers: boolean</p></div>
  <div class="tab-pane"><h2>Példa</h2><div class="card">Static Header</div></div>
</div>
<p>Utána</p>
"""


def test_an_inactive_tab_that_only_repeats_visible_content_yields_no_block():
    parsed = parse_blocks(f"<html><body>{TABS}</body></html>")
    assert [b.text for b in parsed] == ["Oldal", "Példa", "Static Header", "API",
                                        "closeOthers: boolean", "Utána"]
    assert [b.ordinal for b in parsed] == [1, 2, 3, 4, 5, 6]
    assert parsed[-1].heading_path == ["Oldal", "API"]


def page_of(sections, per_section, title=True):
    """Title, egy H1, utána `sections` darab H2-szakasz, mindegyikben `per_section` bekezdés."""
    blocks = [{"id": "b0", "kind": "title", "heading_path": [], "text": "T"}] if title else []
    blocks.append({"id": "b1", "kind": "heading", "heading_path": ["H1"], "text": "H1"})
    for s in range(sections):
        path = ["H1", f"S{s}"]
        blocks.append({"id": f"s{s}", "kind": "heading", "heading_path": path, "text": f"S{s}"})
        blocks += [{"id": f"s{s}p{i}", "kind": "paragraph", "heading_path": path, "text": "x"}
                   for i in range(per_section)]
    return blocks


def test_short_pages_are_one_chunk():
    page = page_of(3, 20)
    assert chunk_blocks(page) == [page]


def test_long_pages_split_at_top_level_headings_with_the_title_in_each_chunk():
    page = page_of(6, 29)                        # 1 + 1 + 6 × 30 = 182 blokk
    chunks = chunk_blocks(page)
    assert [len(c) for c in chunks] == [62, 61, 61]
    assert all(c[0]["kind"] == "title" for c in chunks)
    assert [c[1]["id"] for c in chunks] == ["b1", "s2", "s4"]
    assert [b["id"] for c in chunks for b in c[1:]] == [b["id"] for b in page[1:]]
    assert max(len(c) for c in chunks) <= CHUNK_MAX_BLOCKS


def test_a_section_bigger_than_a_chunk_is_cut_further():
    page = page_of(1, 200)                       # nincs második H2: 70-esével vágva
    chunks = chunk_blocks(page)
    assert all(len(c) <= CHUNK_MAX_BLOCKS for c in chunks)
    assert [b["id"] for c in chunks for b in c[1:]] == [b["id"] for b in page[1:]]


def test_inline_elements_join_like_rendered_text():
    """Szó közepén kezdődő kiemelés nem töri a szót, a lágy kötőjel kiesik; két közvetlenül
    egymást követő link két címke; a <br> szóköz."""
    parsed = parse_blocks(
        "<html><body><main><p><strong>SEO &amp; Techn</strong>ikai alapok, "
        "kereső­optimalizálás</p><p><a href='/a/'>Mérés</a><a href='/b/'>Kiss Anna</a>"
        "</p><p>Első<br>második</p><table><tr><td><b>Tech</b>nika</td></tr></table>"
        "</main></body></html>")
    assert [b.text for b in parsed] == ["SEO & Technikai alapok, keresőoptimalizálás",
                                        "Mérés Kiss Anna", "Első második", "Technika"]
    assert parsed[1].anchors == ["Mérés", "Kiss Anna"]


def test_webshop_menu_ids_overlay_panels_and_tab_separated_tables():
    """A menü technikai azonosítója (a stíluslap rejti) nem kerül a szövegbe; a zárt
    lenyíló panel (kategóriafa, bejelentkezés) chrome; a tabulátorral tagolt, `<br>`-rel tört
    műszaki adatok táblázatsorok, a forráskód behúzása nem cellahatár."""
    page = (
        "<html><body>"
        "<div class='hamburger__dropdown dropdown--content'><ul class='responsive_menu'>"
        "<li><span class='ajax_param'>316001|709257</span><a href='/sct/1'>Klíma</a></li>"
        "</ul></div>"
        "<div class='profile__dropdown dropdown--content'><form><p>Belépés</p>"
        "<input type='password'></form></div>"
        "<main><h1>Termék</h1>"
        "<ul class='dropdown-menu'><li>Demó menüpont</li></ul>"
        "<div class='desc'>\n\t\t\tCold Plasma ionizátor<br>Wi-fi<br>Turbo funkció\n\t\t</div>"
        "<div class='specs'>Tulajdonság\tAdat<br>Kivitel\t<br>Oldalfali klíma<br>"
        "Hűtőteljesítmény\t<br>2.5 kW</div>"
        "</main></body></html>")
    parsed = parse_blocks(page)
    assert not any("316001" in b.text for b in parsed)
    assert [b.region for b in parsed if b.text in ("Klíma", "Belépés")] == ["chrome", "chrome"]
    assert [b.region for b in parsed if b.text == "Demó menüpont"] == ["content"]
    rows = [b for b in parsed if b.kind == "table_row"]
    assert [b.text for b in rows] == ["Tulajdonság | Adat", "Kivitel | Oldalfali klíma",
                                      "Hűtőteljesítmény | 2.5 kW"]
    assert rows[1].cells == [{"header": "Tulajdonság", "value": "Kivitel"},
                             {"header": "Adat", "value": "Oldalfali klíma"}]
    assert "Cold Plasma ionizátor Wi-fi Turbo funkció" in [b.text for b in parsed]
