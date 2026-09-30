"""A kapu és az ellenőrzés mérése (tests/acceptance/gate_eval.py), hálózat nélkül."""
import json

import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se

MODEL = "m"


def block(i, kind, text):
    return {"id": f"b{i}", "kind": kind, "heading_path": [], "text": text}


def mention(block_id, surface, name, kind="concept"):
    return {"block_id": block_id, "surface_form": surface, "canonical_name": name, "type": kind,
            "subtype": None, "description": "d"}


def item(canonical, kind, block_id, text, aliases=()):
    return {"canonical": canonical, "type": kind, "aliases": list(aliases),
            "surface_forms": [{"block": block_id, "text": text}]}


MAIN_PAGE = {
    "page_id": "kk_coach_meres_hu", "lang": "hu-HU", "blocks": [
        block(0, "title", "Bérszámfejtés és készletforgás"),
        block(1, "heading", "Bérszámfejtés Csomag"),
        block(2, "paragraph", "A készletforgási sebesség és a raktári rendetlenség. Havi zárás."),
        block(3, "paragraph", "A készletforgási sebességet a Számlázó méri."),
    ],
    "gold": {"primary_entities": [], "negatives": [{"text": "raktári rendetlenség"}],
             "entities": [item("Bérszámfejtés Csomag", "service", "b1", "Bérszámfejtés Csomag"),
                          item("készletforgási sebesség", "concept", "b2",
                               "készletforgási sebesség"),
                          item("Számlázó", "tech", "b3", "Számlázó")],
             "optional": [item("havi zárás", "concept", "b2", "Havi zárás")]}}
REGRESSION_PAGE = {
    "page_id": "materia_etlap_hu", "lang": "hu-HU",
    "blocks": [block(0, "heading", "Pizza Margherita"), block(1, "paragraph", "D.O.P. sajt")],
    "gold": {"primary_entities": [], "negatives": [], "optional": [],
             "entities": [item("Pizza Margherita", "product", "b0", "Pizza Margherita")]}}
FULL = {"call_ids": [], "primary_entities": [], "entities": [
    mention("b1", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
    mention("b2", "készletforgási sebesség", "Készletforgási sebesség"),
    mention("b3", "készletforgási sebességet", "Készletforgási sebesség"),
    mention("b2", "raktári rendetlenség", "Raktári rendetlenség"),
    mention("b2", "Havi zárás", "Havi zárás"),
    mention("b3", "Számlázó", "Számlázó", "tech"),
    mention("b3", "Számlázó", "Méri", "tech"),
]}
REGRESSION = {"call_ids": [], "primary_entities": [], "entities": [
    mention("b0", "Pizza Margherita", "Pizza Margherita", "product"),
    mention("b1", "D.O.P.", "D.O.P.")]}


def write(tmp_path, page_id, tag, record):
    (tmp_path / "synthetic").mkdir(exist_ok=True)
    ge.write_record(tmp_path, page_id, MODEL, tag, record)


def test_verdicts_export_once_per_page_name_and_type_and_keep_the_filled_ones(tmp_path):
    write(tmp_path, "kk_coach_meres_hu", "a", FULL)
    fewer = {**FULL, "entities": FULL["entities"][:3]}
    write(tmp_path, "kk_coach_meres_hu", "b", fewer)
    path = tmp_path / "verdicts.json"
    assert ge.export_verdicts(MODEL, tmp_path, [MAIN_PAGE], ["a", "b"], path) == 4
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [(i["canonical"], i["type"], i["block"], i["kind"], i["reference"], i["verdict"])
            for i in data["items"]] == [
        ("Bérszámfejtés Csomag", "service", "b1", "heading", "kötelező: Bérszámfejtés Csomag",
         None),
        ("Készletforgási sebesség", "concept", "b2", "paragraph",
         "kötelező: készletforgási sebesség", None),
        ("Raktári rendetlenség", "concept", "b2", "paragraph", "negatív „raktári rendetlenség”",
         None),
        ("Havi zárás", "concept", "b2", "paragraph", "opcionális: havi zárás", None)]
    assert data["items"][1]["block_text"].startswith("A készletforgási sebesség")
    data["items"][0]["verdict"] = "valid"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    other = {**FULL, "entities": [mention("b2", "Havi zárás", "Havi zárás", "service")]}
    write(tmp_path, "kk_coach_meres_hu", "c", other)
    assert ge.export_verdicts(MODEL, tmp_path, [MAIN_PAGE], ["a", "b", "c"], path) == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["items"][0]["verdict"] == "valid"
    assert (data["items"][-1]["canonical"], data["items"][-1]["type"]) == ("Havi zárás",
                                                                           "service")


VERDICTS = {("kk_coach_meres_hu", "berszamfejtes csomag", "service"): "valid",
            ("kk_coach_meres_hu", "keszletforgasi sebesseg", "concept"): "valid",
            ("kk_coach_meres_hu", "raktari rendetlenseg", "concept"): "descriptive",
            ("materia_etlap_hu", "d.o.p.", "concept"): "valid"}


def test_soft_precision_from_verdicts_with_the_heading_items_first():
    score = ge.score_soft(MAIN_PAGE, FULL, VERDICTS)
    assert (score.items, score.valid, score.judged_total) == (4, 2, 3)
    assert score.by_type == {"service": [1, 1], "concept": [1, 2]}
    assert score.unjudged == [("Havi zárás", "concept")]
    context = ge.PageContext(MAIN_PAGE["blocks"])
    items = ge.soft_items(FULL["entities"], context.by_id())
    assert [i.canonical for i in ge.ranked(items, context)] == [
        "Bérszámfejtés Csomag", "Készletforgási sebesség",   # heading, title-beli név; 2 említés
        "Raktári rendetlenség", "Havi zárás"]
    assert score.top == {10: [2, 3], 20: [2, 3]}


def test_dropped_reference_items():
    gated = {**FULL, "entities": [e for e in FULL["entities"]
                                  if e["canonical_name"] not in ("Havi zárás",
                                                                 "Raktári rendetlenség")]}
    assert ge.dropped_references(MAIN_PAGE, FULL, gated) == [
        ("Havi zárás", "opcionális: havi zárás")]


# A megszűnt E1-futtató (M2/5) tárolt kimenete ezekre a rekordokra: a kapu utáni rekord és a
# döntések; a report ilyen tárolt kimenetet olvas.
E1_DECISIONS = {
    "kk_coach_meres_hu": [
        {"key": "berszamfejtes csomag", "canonical": "Bérszámfejtés Csomag", "type": "service",
         "mentions": 1, "structure": "heading:b1", "blocks": 1, "knowledge": None, "keep": True},
        {"key": "keszletforgasi sebesseg", "canonical": "Készletforgási sebesség",
         "type": "concept", "mentions": 2, "structure": None, "blocks": 2, "knowledge": None,
         "keep": True},
        {"key": "raktari rendetlenseg", "canonical": "Raktári rendetlenség", "type": "concept",
         "mentions": 1, "structure": None, "blocks": 1, "knowledge": None, "keep": False},
        {"key": "havi zaras", "canonical": "Havi zárás", "type": "concept", "mentions": 1,
         "structure": None, "blocks": 1, "knowledge": "wikidata:hu:Q1", "keep": True}],
    "materia_etlap_hu": [
        {"key": "d.o.p.", "canonical": "D.O.P.", "type": "concept", "mentions": 1,
         "structure": None, "blocks": 1, "knowledge": None, "keep": False}]}


def stored_e1(tmp_path, page_id, record):
    dropped = {d["canonical"] for d in E1_DECISIONS[page_id] if not d["keep"]}
    write(tmp_path, page_id, "cp-e1", {**record, "gate_source": "cp", "entities": [
        e for e in record["entities"] if e["canonical_name"] not in dropped]})
    ge.decisions_path(tmp_path, page_id, MODEL, "cp-e1").write_text(
        json.dumps(E1_DECISIONS[page_id], ensure_ascii=False), encoding="utf-8")


def test_report_main_row_regression_row_conditions_and_dropped(tmp_path, monkeypatch):
    write(tmp_path, "kk_coach_meres_hu", "cp", FULL)
    write(tmp_path, "materia_etlap_hu", "cp", REGRESSION)
    pages = [MAIN_PAGE, REGRESSION_PAGE]
    stored_e1(tmp_path, "kk_coach_meres_hu", FULL)
    stored_e1(tmp_path, "materia_etlap_hu", REGRESSION)
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps({"items": [
        {"page_id": p, "canonical": n, "type": t, "verdict": v}
        for (p, n, t), v in VERDICTS.items()]}), encoding="utf-8")
    text = ge.report_markdown(MODEL, tmp_path, pages, {"nincs": "cp", "E1": "cp-e1"}, path)
    main = text.split("## fő sor: kk.coach + ngx")[1].split("## regresszió")[0]
    assert ("| nincs (`cp`) | 100.0 · 100.0 | 100.0 (3/3) | 100.0 (2/2) | 66.7 (2/3) | "
            "50.0 (1/2) · 100.0 (1/1) | 66.7 (2/3) | 66.7 (2/3) | 1 | 0.0000 |") in main
    assert ("| E1 (`cp-e1`) | 100.0 · 100.0 | 100.0 (3/3) | 100.0 (2/2) | 100.0 (2/2) | "
            "100.0 (1/1) · 100.0 (1/1) | 100.0 (2/2) | 100.0 (2/2) | 1 | 0.0000 |") in main
    regression = text.split("## regresszió: Materia")[1].split("## Oldalanként")[0]
    assert "| nincs (`cp`) | 100.0 · — | 100.0 (1/1) | 100.0 (1/1) | 100.0 (1/1) |" in regression
    assert "- **E1** (0): —" in text
    assert ("**fő sor: kk.coach + ngx**\n\n- **E1** (kiesett 1): descriptive 1\n\n"
            "**regresszió: Materia**\n\n- **E1** (kiesett 1): valid 1") in text
    assert ("- concept (3 tétel; marad 2, kiesik 1):\n"
            "  - (a) szerkezet: 0 teljesül / 3 nem; csak ez tartja meg: 0\n"
            "  - (b) ismétlődés: 1 teljesül / 2 nem; csak ez tartja meg: 1\n"
            "  - (c) tudásbázis: 1 teljesül / 2 nem; csak ez tartja meg: 1\n"
            "- service (1 tétel; csak (a) számít): 1 teljesül / 0 nem") in text
    assert "- **nincs** (1): Havi zárás (concept; kk_coach_meres_hu)" in text


def test_pages_split_into_main_and_regression():
    assert set(ge.MAIN) | set(ge.REGRESSION) == {
        p["page_id"] for p in se.load_pages(page_set=se.REAL)}


def test_realign_moves_mentions_keeps_fabricated_ones_and_updates_verdict_blocks(tmp_path):
    old = [block(0, "title", "Árazás"), block(1, "paragraph", "Project"),
           block(2, "paragraph", "Bérszámfejtés Csomag"), block(3, "paragraph", "120 Ft"),
           block(4, "paragraph", "Törölt")]
    new = [block(0, "title", "Árazás"),
           {"id": "b1", "kind": "table_row", "heading_path": [], "text": "Project"},
           {"id": "b2", "kind": "table_row", "heading_path": [],
            "text": "Bérszámfejtés Csomag | 120 Ft"}]
    record = {"entities": [
        mention("b2", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
        mention("b1", "nincs itt", "Kitalált"),
        mention("b4", "Törölt", "Törölt")]}
    moved, changes = ge.realign_record(record, old, new)
    assert [(e["canonical_name"], e["block_id"]) for e in moved["entities"]] == [
        ("Bérszámfejtés Csomag", "b2"), ("Kitalált", "b1")]
    assert changes == ["Törölt: b4 kimarad"]
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"items": [
        {"page_id": "p", "canonical": "Bérszámfejtés Csomag", "type": "service", "block": "b2",
         "kind": "paragraph", "block_text": "Bérszámfejtés Csomag", "verdict": "valid"}]}),
        encoding="utf-8")
    assert ge.realign_verdicts(path, {"page_id": "p", "blocks": new}, moved) == 1
    entry = json.loads(path.read_text(encoding="utf-8"))["items"][0]
    assert (entry["block"], entry["kind"], entry["block_text"], entry["verdict"]) == (
        "b2", "table_row", "Bérszámfejtés Csomag | 120 Ft", "valid")
