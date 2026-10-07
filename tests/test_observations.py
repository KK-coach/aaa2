"""Három megfigyelés a tárolt állapot érvényességéről, szintetikus site-on, hálózat és valódi
LLM nélkül: a blokkok újraépítése a renderelt szerkezet változásakor, a tárolt kinyerés
visszaírása megváltozott oldalra, és az entitás típusa több oldal rekordjából."""
import zstandard

from aaa2.entities.dom import build_blocks
from aaa2.entities.extract import clear_entities, restore_llm, restore_plan, run_llm
from aaa2.entities.rules import run_rules
from tests.test_entities_extract import client_for, mention, reply
from tests.test_entities_rules import html, site


def rerender(con, page_id, body, title="Cikk", main_content=None):
    """Az oldal újra renderelve: a renderelt DOM (és ha adott, a fő tartalom) változik, a nyers
    HTML hash-e nem (a tartalmat JavaScript tölti be)."""
    blob = zstandard.ZstdCompressor().compress(html(title, body).encode())
    con.execute("UPDATE pages SET rendered_html = ? WHERE page_id = ?", [blob, page_id])
    if main_content is not None:
        con.execute("UPDATE pages SET main_content = ? WHERE page_id = ?",
                    [main_content, page_id])


def kinds(con, page_id):
    return con.execute("SELECT kind, level, text FROM blocks WHERE page_id = ? AND region = "
                       "'content' ORDER BY ordinal", [page_id]).fetchall()


def test_a_heading_that_became_a_paragraph_rebuilds_the_blocks():
    # 1. megfigyelés: a nyers HTML és a lapos szöveg változatlan, a renderelt <h2> bekezdés lett
    con = site({"/": html("Cikk", "<main><h1>Cikk</h1><h2>Mérés</h2><p>A mérés alapjai.</p>"
                          "</main>")})
    con.execute("UPDATE pages SET raw_html_hash = 'h-' || page_id")
    build_blocks(con)
    assert ("heading", 2, "Mérés") in kinds(con, 1)
    before = con.execute("SELECT raw_html_hash, title, h1, main_content FROM pages").fetchall()
    rerender(con, 1, "<main><h1>Cikk</h1><p>Mérés</p><p>A mérés alapjai.</p></main>")
    assert con.execute("SELECT raw_html_hash, title, h1, main_content FROM pages"
                       ).fetchall() == before               # a hash és a lapos szöveg ugyanaz
    assert build_blocks(con) == 1
    assert ("heading", 2, "Mérés") not in kinds(con, 1)
    assert ("paragraph", None, "Mérés") in kinds(con, 1)
    assert build_blocks(con) == 0                            # utána nyugalom
    # a szerkezetet nem érintő újrarenderelés (más attribútum) nem épít újra
    rerender(con, 1, "<main data-x='1'><h1>Cikk</h1><p>Mérés</p><p>A mérés alapjai.</p></main>")
    ids = con.execute("SELECT block_id FROM blocks ORDER BY block_id").fetchall()
    assert build_blocks(con) == 0
    assert con.execute("SELECT block_id FROM blocks ORDER BY block_id").fetchall() == ids


def test_a_stored_extraction_is_not_restored_onto_a_page_that_gained_a_block(tmp_path):
    # 2. megfigyelés: a régi említések az új szövegben is megvannak, de az oldalon új szakasz
    # áll, amelyet a kinyerés nem látott: a régi rekord nem írható vissza aktuálisként
    con = site({"/": html("Cikk", "<main><h1>Cikk</h1><p>A webanalitika a mérés alapja.</p>"
                          "</main>")})
    con.execute("UPDATE pages SET raw_html_hash = 'h-' || page_id")
    client, adapter = client_for(con, [reply(mention("b2", "webanalitika", "Webanalitika",
                                                     "concept"))], tmp_path)
    run_llm(con, client)
    assert restore_plan(con)[1][0] == "restore"              # változatlan oldal: visszaírható
    rerender(con, 1, "<main><h1>Cikk</h1><p>A webanalitika a mérés alapja.</p>"
             "<h2>Új szakasz</h2><p>A konverzióoptimalizálás a következő lépés.</p></main>",
             main_content="A webanalitika a mérés alapja. Új szakasz A konverzióoptimalizálás "
                          "a következő lépés.")
    assert build_blocks(con) == 1
    texts = [text for _, _, text in kinds(con, 1)]
    assert "A webanalitika a mérés alapja." in texts and "Új szakasz" in texts
    status, stored, _ = restore_plan(con)[1]
    assert stored is not None and status == "restore_input_changed"
    clear_entities(con)
    run_rules(con)
    restored = restore_llm(con)
    assert (restored.pages, restored.skipped) == (0, {"restore_input_changed": 1})
    assert len(adapter.calls) == 1


def test_the_type_of_a_shared_name_is_the_type_of_the_first_pages_record(tmp_path):
    # 3. megfigyelés (fennáll, döntésre vár): az azonos nevű említések egy entitásba kerülnek,
    # az entitás típusa az első oldal rekordjáé. A cikk saját rekordja fogalmat mond a
    # „mérés”-re; ha az előtte álló ajánlatoldal rekordja szolgáltatásnak nevezi, a cikk
    # említése is szolgáltatás típusú entitásé lesz. A saját rekord típusa csak szavazat
    # (`type_votes`); a típust nem írja át. A végállapot a tárolt rekordokból vezethető le
    # (nem függ a futások történetétől), de egy másik oldal új rekordja megváltoztatja.
    def built(offer_kind, article_kind):
        folder = tmp_path / f"{offer_kind}-{article_kind}"
        folder.mkdir()
        con = site({"/": html("Ajánlat", "<main><h1>Ajánlat</h1><p>A mérés a szolgáltatásunk."
                              "</p></main>"),
                    "/blog/": html("Cikk", "<main><h1>Cikk</h1><p>A mérés fogalma.</p></main>")})
        client, _ = client_for(con, [reply(mention("b2", "mérés", "mérés", offer_kind)),
                                     reply(mention("b2", "mérés", "mérés", article_kind))],
                               folder)
        run_llm(con, client)
        return con.execute(
            "SELECT p.url, e.type, e.type_votes, e.type_suggested FROM page_entities pe JOIN "
            "entities e USING (entity_id) JOIN pages p ON p.page_id = pe.page_id WHERE "
            "e.name = 'mérés' ORDER BY p.url").fetchall()
    article = "https://pelda.hu/blog/"
    same = {url: kind for url, kind, _, _ in built("concept", "concept")}
    assert same[article] == "concept"
    mixed = built("service", "concept")
    assert {url: kind for url, kind, _, _ in mixed}[article] == "service"
    assert {votes for _, _, votes, _ in mixed} == {'{"concept": 1, "service": 1}'}
    assert {suggested for *_, suggested in mixed} == {"service"}     # holtverseny: a mostani
    # a sorrend dönt: ha az első oldal rekordja mond fogalmat, mindkét oldalon fogalom marad
    assert {kind for _, kind, _, _ in built("concept", "service")} == {"concept"}
