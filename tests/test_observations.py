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


def shared_name_site(tmp_path, offer_kind, article_kind, head=""):
    """Két oldal ugyanazzal a névvel: az ajánlatoldal egyszer, a cikk kétszer említi a
    „mérés”-t; a kinyerés oldalanként más típust mond rá."""
    folder = tmp_path / f"{offer_kind}-{article_kind}-{bool(head)}"
    folder.mkdir()
    con = site({"/": html("Ajánlat", "<main><h1>Ajánlat</h1><p>A mérés a szolgáltatásunk.</p>"
                          "</main>", head=head),
                "/blog/": html("Cikk", "<main><h1>Cikk</h1><p>A mérés fogalma.</p>"
                               "<p>A mérés a gyakorlatban.</p></main>")})
    if head:
        run_rules(con)
    client, _ = client_for(con, [
        reply(mention("b2", "mérés", "mérés", offer_kind)),
        reply(mention("b2", "mérés", "mérés", article_kind),
              mention("b3", "mérés", "mérés", article_kind))], folder)
    run_llm(con, client)
    return con.execute(
        "SELECT DISTINCT e.type, e.type_votes, e.type_changed_from, e.source FROM "
        "page_entities pe JOIN entities e USING (entity_id) WHERE lower(e.name) = 'mérés'"
    ).fetchall()


def test_the_type_of_a_shared_name_is_the_majority_of_the_votes(tmp_path):
    # 3. megfigyelés: az azonos nevű említések egy entitásba kerülnek; a típus a kinyerés
    # szavazatainak többsége, nem az első oldal rekordjáé
    assert shared_name_site(tmp_path, "service", "concept") == [
        ("concept", '{"concept": 2, "service": 1}', "service", "llm")]
    # a sorrend nem számít: ha az első oldal mond technológiát és a másik két említés fogalmat
    assert shared_name_site(tmp_path, "tech", "concept") == [
        ("concept", '{"concept": 2, "tech": 1}', "tech", "llm")]
    # szolgáltatássá a többség nem léptet elő: azt a feloldó dönti el az oldalakból
    assert shared_name_site(tmp_path, "concept", "service") == [
        ("concept", '{"concept": 1, "service": 2}', None, "llm")]
    assert shared_name_site(tmp_path, "concept", "concept") == [
        ("concept", '{"concept": 3}', None, "llm")]


def test_a_type_from_structured_data_is_not_overridden_by_the_votes(tmp_path):
    from tests.test_entities_rules import ld

    service = ld({"@type": "Service", "name": "Mérés", "url": "https://pelda.hu/"})
    found = shared_name_site(tmp_path, "concept", "concept", head=service)
    assert [(kind, source) for kind, _, _, source in found] == [("service", "schema")]
    assert found[0][1] == '{"concept": 3}' and found[0][2] is None


def test_a_tie_keeps_the_current_type_and_otherwise_the_fixed_order_decides():
    from aaa2.entities.extract import majority_type

    assert majority_type({"concept": 2, "service": 2}, "service") == "service"
    assert majority_type({"concept": 2, "service": 2}, "concept") == "concept"
    # a jelenlegi típus nincs a legjobbak között: a típusok rögzített sorrendje dönt
    first = majority_type({"tech": 2, "service": 2, "org": 1}, "org")
    assert first == majority_type({"service": 2, "tech": 2, "org": 1}, "org")
    assert first in ("service", "tech")
