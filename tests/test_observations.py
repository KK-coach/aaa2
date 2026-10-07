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


def shared_name_site(tmp_path, offer_kind, article_kind, head="", bound=False):
    """Két oldal ugyanazzal a névvel: az ajánlatoldal egyszer, a cikk kétszer említi a
    „mérés”-t; a kinyerés oldalanként más típust mond rá. A végén a feloldás utáni címke
    (`apply_majority_types`); `bound`: előtte az entitás oldalhoz kötve. Visszaad: (tárolt
    típus, címke, szavazatok, forrás)."""
    from aaa2.entities.extract import apply_majority_types

    folder = tmp_path / f"{offer_kind}-{article_kind}-{bool(head)}-{bound}"
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
    if bound:
        con.execute("UPDATE entities SET anchor_page_id = 1 WHERE lower(name) = 'mérés'")
    apply_majority_types(con)
    apply_majority_types(con)                     # újrafuttatva ugyanaz
    return con.execute(
        "SELECT DISTINCT e.type, e.label_type, e.type_votes, e.source FROM page_entities pe "
        "JOIN entities e USING (entity_id) WHERE lower(e.name) = 'mérés'").fetchall()


def test_the_type_of_a_shared_name_is_labelled_by_the_majority_of_the_votes(tmp_path):
    # 3. megfigyelés: az azonos nevű említések egy entitásba kerülnek, a tárolt típusa az első
    # oldal rekordjáé; a feloldás utáni címke a szavazatok többségét írja ki, külön mezőben
    assert shared_name_site(tmp_path, "tech", "concept") == [
        ("tech", "concept", '{"concept": 2, "tech": 1}', "llm")]
    # a sorrend nem számít: ha az első oldal mond fogalmat és a másik két említés technológiát
    assert shared_name_site(tmp_path, "concept", "tech") == [
        ("concept", "tech", '{"concept": 1, "tech": 2}', "llm")]
    # ahol a többség a tárolt típus, nincs címke
    assert shared_name_site(tmp_path, "concept", "concept") == [
        ("concept", None, '{"concept": 3}', "llm")]
    # szolgáltatássá a többség nem léptet elő: azt a feloldó dönti el az oldalakból
    assert shared_name_site(tmp_path, "concept", "service") == [
        ("concept", None, '{"concept": 1, "service": 2}', "llm")]


def test_the_label_leaves_structured_data_types_and_page_bound_entities_alone(tmp_path):
    from tests.test_entities_rules import ld

    service = ld({"@type": "Service", "name": "Mérés", "url": "https://pelda.hu/"})
    assert shared_name_site(tmp_path, "concept", "concept", head=service) == [
        ("service", None, '{"concept": 3}', "schema")]
    # az oldalhoz kötött entitás típusát az oldal szerkezete adja
    assert shared_name_site(tmp_path, "tech", "concept", bound=True) == [
        ("tech", None, '{"concept": 2, "tech": 1}', "llm")]


def test_a_tie_is_decided_by_the_fixed_order_with_the_concept_first():
    from aaa2.entities.extract import majority_type

    # a konkrétabb típushoz többség kell; a jelenlegi típus nem számít
    assert majority_type({"concept": 2, "tech": 2}, "tech") == "concept"
    assert majority_type({"tech": 2, "concept": 2}, "concept") == "concept"
    assert majority_type({"product": 1, "concept": 1}, "product") == "concept"
    # fogalom nélkül a típusok rögzített sorrendje, a szavazatok sorrendjétől függetlenül
    first = majority_type({"tech": 2, "org": 2, "place": 1}, "place")
    assert first == majority_type({"org": 2, "tech": 2, "place": 1}, "tech")
    assert majority_type({"concept": 1, "tech": 3}, "concept") == "tech"


def test_the_label_is_what_the_outputs_show_and_the_stored_type_is_what_the_resolver_sees(
        tmp_path):
    from aaa2.entities.extract import apply_majority_types
    from aaa2.entities.report import entity_table

    con = site({"/": html("A", "<main><h1>A</h1><p>A mérőpult a cég eszköze.</p></main>"),
                "/b/": html("B", "<main><h1>B</h1><p>A mérőpult fogalma.</p>"
                            "<p>A mérőpult a gyakorlatban.</p></main>")})
    (tmp_path / "x").mkdir()
    client, _ = client_for(con, [
        reply(mention("b2", "mérőpult", "mérőpult", "tech", "software")),
        reply(mention("b2", "mérőpult", "mérőpult", "concept"),
              mention("b3", "mérőpult", "mérőpult", "concept"))], tmp_path / "x")
    run_llm(con, client)
    apply_majority_types(con)
    assert con.execute("SELECT type, subtype, label_type, label_subtype FROM entities WHERE "
                       "name = 'mérőpult'").fetchone() == ("tech", "software", "concept", None)
    (row,) = [r for r in entity_table(con) if r["entity"] == "mérőpult"]
    assert (row["type"], row["subtype"], row["type_votes"]) == (
        "concept", "", "concept: 2; tech: 1")


def test_resolving_twice_gives_the_same_entities_merges_and_main_entities(tmp_path):
    # a címke külön mezőben áll: a feloldás másodszor is a tárolt típust látja, a címkézett
    # típus nem szivárog vissza az összevonásokba. A „Cloudflare” tárolt típusa technológia
    # (az első rekordé), a címkéje szervezet (a többség); a „Cloudflare, Inc.” szervezet. Ha a
    # címke a tárolt típust írná át, a második feloldás a két szervezetet összevonná.
    from aaa2 import api
    from aaa2.functions.graph import build_graph

    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>A Cloudflare a hálózatunk.</p></main>"),
                "/a/": html("A cikk", "<main><h1>A cikk</h1><p>A Cloudflare egy cég.</p>"
                            "<p>A Cloudflare székhelye San Francisco.</p></main>"),
                "/b/": html("B cikk", "<main><h1>B cikk</h1><p>A Cloudflare, Inc. részvényei."
                            "</p></main>")})
    (tmp_path / "x").mkdir()
    client, _ = client_for(con, [
        reply(mention("b2", "Cloudflare", "Cloudflare", "tech", "software")),
        reply(mention("b2", "Cloudflare", "Cloudflare", "org", "company"),
              mention("b3", "Cloudflare", "Cloudflare", "org", "company")),
        reply(mention("b2", "Cloudflare, Inc.", "Cloudflare, Inc.", "org", "company"))],
        tmp_path / "x")
    run_llm(con, client)
    target = api.Site(con, tmp_path / "pelda.hu.duckdb", "pelda")

    def state():
        build_graph(con)
        return (
            con.execute("SELECT name, type, subtype, label_type, label_subtype, list_sort("
                        "aliases) FROM entities ORDER BY ALL").fetchall(),
            con.execute("SELECT DISTINCT kept_name, removed_name, rule FROM merge_log ORDER BY "
                        "ALL").fetchall(),
            con.execute("SELECT e.name, a.alias, a.source FROM entity_aliases a JOIN entities e "
                        "USING (entity_id) ORDER BY ALL").fetchall(),
            con.execute("SELECT n.url, e.name, m.role, m.confidence FROM page_main_entity m "
                        "JOIN page_nodes n USING (page_id) JOIN entities e USING (entity_id) "
                        "ORDER BY ALL").fetchall())

    api.resolve(target, knowledge=False)
    first = state()
    names = {row[0]: row[1:4] for row in first[0]}
    assert names["Cloudflare"] == ("tech", "software", "org")          # tárolt típus, címke
    assert names["Cloudflare, Inc."][:1] == ("org",) and names["Cloudflare, Inc."][2] is None
    api.resolve(target, knowledge=False)
    assert state() == first
    api.resolve(target, knowledge=False)
    assert state() == first
