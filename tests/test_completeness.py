"""A crawl teljességi állapota egy forrásból (`crawl.completeness`), és a hiány típusú
megállapítások: hiányt csak ellenőrzött célról állítanak."""
from aaa2.db.connect import connect
from aaa2.engine import queries
from aaa2.engine.frontier import record_missing_mode
from aaa2.functions.facts import site_fact_rows, sitemap_rows
from tests.test_blind_fixes import BASE, analysed, product
from tests.test_crawl import run, site, tools  # noqa: F401  (fixture-ök)
from tests.test_current_pages import small
from tests.test_entities_rules import site as stored_site


async def test_a_crawl_that_hits_the_page_limit_says_so(site, tools):  # noqa: F811
    con = connect(":memory:")
    small(site)                             # három sitemap-cím, kettes korlát
    summary = await run(con, site, tools, max_pages=2)
    assert summary.pages_done == 2
    assert summary.stopped == "oldalkorlát: a sor elérte a 2 címet, 1 cím kimaradt"
    run_row = queries.crawl_runs(con)[-1]
    assert (run_row.skipped_by_limit, run_row.stopped, run_row.mode, run_row.include,
            run_row.exclude) == (1, summary.stopped, "links", "", "")
    assert "megállt: oldalkorlát" in run_row.notes
    state = queries.completeness(con)
    assert state.limited and not state.complete
    assert state.states == ("oldalkorláton vagy idő előtt megállt",)
    # a tényfájlok ugyanebből a lekérdezőből: a korlát, a kimaradt címek és a részleges jelölés
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(con)}
    assert facts["a crawl az oldalkorláton állt meg"] == "igen"
    assert facts["crawl: a korlát miatt kimaradt címek"] == 1
    assert facts["crawl: a megállás oka"] == summary.stopped
    assert facts["crawl: a bejárás teljessége"] == "oldalkorláton vagy idő előtt megállt"
    assert {row["bejárás"] for row in sitemap_rows(con)} == {"részleges"}


async def test_a_complete_scoped_crawl_records_its_scope(site, tools):  # noqa: F811
    con = connect(":memory:")
    small(site)
    summary = await run(con, site, tools, exclude="/old/")
    assert summary.stopped is None
    state = queries.completeness(con)
    assert (state.skipped_by_limit, state.exclude, state.include) == (0, "/old/", "")
    assert not state.limited and state.scoped and not state.complete
    assert state.states == ("hatókörrel szűkített",)
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(con)}
    assert facts["crawl: hatókör (exclude)"] == "/old/"
    assert facts["a crawl az oldalkorláton állt meg"] == "nem"


def test_earlier_runs_fall_back_to_the_queue_size_and_take_the_scope_once():
    con = connect(":memory:")
    con.execute("INSERT INTO crawl_runs (started_at, finished_at, max_pages, notes) VALUES "
                "(current_timestamp, current_timestamp, 2, 'új: x')")
    assert queries.completeness(con).limited is False
    for path in ("/", "/a/"):
        con.execute("INSERT INTO crawl_queue (url, status) VALUES (?, 'done')", [BASE + path])
    state = queries.completeness(con)
    # a kimaradt címek számát nem rögzítő futás: a sor elérte a korlátot
    assert state.skipped_by_limit is None and state.limited
    assert record_missing_mode(con, True, "/bolt/", None) == 1
    state = queries.completeness(con)
    assert (state.mode, state.include, state.exclude, state.sitemap_mode) == (
        "sitemap", "/bolt/", "", True)
    record_missing_mode(con, False, "/mas/", "/x/")           # a rögzített érték marad
    assert queries.completeness(con).include == "/bolt/"
    assert queries.completeness(connect(":memory:")).states == ()


LEGAL_FOOTER = "".join(f"<a href='{path}'>jogi</a>" for path in (
    "/aszf", "/adatvedelem", "/elallas", "/szallitas", "/garancia", "/impresszum"))


def one_product_site():
    """Egyetlen termékoldal; a lábléce hat jogi oldalra hivatkozik, amelyek nincsenek bejárva."""
    html = product("Levendula szappan 100g", "/levendula-szappan-100g",
                   extra=f"<footer>{LEGAL_FOOTER}</footer>")
    return stored_site({"/levendula-szappan-100g": html})


def legal_findings(con):
    _, found = analysed(con)
    return {f[3]["kind"]: (f[1], f[3]["status"], f[2]) for f in found if f[0] == "legal_page"}


def test_a_linked_but_uncrawled_legal_page_is_not_reported_missing():
    legal = legal_findings(one_product_site())
    assert len(legal) == 6
    assert {status for _, status, _ in legal.values()} == {"linked_unchecked"}
    severity, _, summary = legal["warranty"]
    assert severity == "low"
    assert summary == ("jogi oldal hivatkozva, a cél nincs bejárva (nem ellenőrzött): garancia "
                       f"({BASE}/garancia)")


def test_a_missing_legal_page_on_a_partial_crawl_is_marked_unchecked():
    con = stored_site({"/levendula-szappan-100g": product(
        "Levendula szappan 100g", "/levendula-szappan-100g")})
    complete = legal_findings(con)
    # a közös lábléc az ÁSZF-re és az adatvédelemre hivatkozik: ezek nem ellenőrzöttek, a többi
    # négy fajtára nincs hivatkozás: hiányzik
    assert complete["terms"][1] == "linked_unchecked" and complete["warranty"][1] == "missing"
    assert "részleges" not in complete["warranty"][2]
    con.execute("INSERT INTO crawl_runs (started_at, finished_at, max_pages, skipped_by_limit, "
                "stopped) VALUES (current_timestamp, current_timestamp, 1, 5, 'oldalkorlát')")
    partial = legal_findings(con)
    assert partial["warranty"][2] == "hiányzó jogi oldal: garancia (részleges bejárás: nem " \
                                     "ellenőrzött)"


def test_uncovered_topic_and_missing_page_are_marked_on_a_partial_crawl(monkeypatch):
    from aaa2.functions.findings import build_findings
    from tests.test_findings import built, rows

    con, _ = built(monkeypatch)
    before = {kind: rows(con, kind) for kind in ("uncovered_topic", "missing_page")}
    assert all(len(found) == 1 and "crawl" not in found[0][2] for found in before.values())
    con.execute("INSERT INTO crawl_runs (started_at, finished_at, max_pages, skipped_by_limit, "
                "stopped) VALUES (current_timestamp, current_timestamp, 5, 3, 'oldalkorlát')")
    build_findings(con)
    for kind, found in before.items():
        ((severity, summary, evidence),) = rows(con, kind)
        # a megállapítás megmarad, ugyanazzal a súlyossággal, de nem állít hiányt tényként
        assert severity == found[0][0] and evidence["crawl"] == "partial"
        assert summary == found[0][1] + (" (részleges bejárás: a be nem járt oldalak között "
                                         "lehet saját oldala)")
