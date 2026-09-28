"""Az annotálósablon referencialistájának igazítása új blokkokhoz (tests/acceptance/annotation.py)."""
from tests.acceptance.annotation import block_mapping, realign

OLD = [{"id": "b0", "text": "Cím"}, {"id": "b1", "text": "GA4 GTM Stape.io"},
       {"id": "b2", "text": "Ismétlés"}, {"id": "b3", "text": "Vége a GTM-mel"}]
NEW = [{"id": "b0", "text": "Cím"}, {"id": "b1", "text": "GA4 · GTM · Stape.io"},
       {"id": "b2", "text": "Vége a GTM-mel"}]


def item(canonical, *forms):
    return {"canonical": canonical, "surface_forms": [{"block": b, "text": t} for b, t in forms]}


def test_blocks_pair_by_text_without_the_label_separator():
    assert block_mapping(OLD, NEW) == {"b0": "b0", "b1": "b1", "b3": "b2"}


def test_realign_moves_drops_and_reports():
    gold = {"primary_entities": ["GTM"], "negatives": [{"text": "Ismétlés"}],
            "entities": [item("Google Tag Manager", ("b1", "GTM"), ("b2", "Ismétlés"),
                              ("b3", "GTM-mel"))],
            "optional": [item("GTM Stape", ("b1", "GTM Stape.io"))]}
    out, changes = realign(gold, OLD, NEW)
    assert out["entities"][0]["surface_forms"] == [{"block": "b1", "text": "GTM"},
                                                   {"block": "b2", "text": "GTM-mel"}]
    assert out["optional"][0]["surface_forms"] == []
    assert (out["primary_entities"], out["negatives"]) == (gold["primary_entities"],
                                                           gold["negatives"])
    assert changes == [
        "entities Google Tag Manager: b2 „Ismétlés” kimarad (a blokk nincs meg)",
        "entities Google Tag Manager: b3 → b2",
        "optional GTM Stape: b1 → b1 „GTM Stape.io” kimarad (nem áll az új blokkban)",
        "optional GTM Stape: nem maradt szöveg szerinti alak"]


def test_merged_grid_cells_map_to_the_row_that_contains_them():
    old = [{"id": "b0", "text": "Árazás"}, {"id": "b1", "text": "Project"},
           {"id": "b2", "text": "Est. cost"}, {"id": "b3", "text": "BigQuery integráció"},
           {"id": "b4", "text": "160000 Ft"}, {"id": "b5", "text": "Óradíj"}]
    new = [{"id": "b0", "text": "Árazás"}, {"id": "b1", "text": "Project | Est. cost"},
           {"id": "b2", "text": "BigQuery integráció · GA4 export | 160000 Ft"},
           {"id": "b3", "text": "Óradíj"}]
    assert block_mapping(old, new) == {"b0": "b0", "b1": "b1", "b2": "b1", "b3": "b2",
                                       "b4": "b2", "b5": "b3"}
    out, changes = realign({"entities": [item("BigQuery integráció",
                                               ("b3", "BigQuery integráció"))]}, old, new)
    assert out["entities"][0]["surface_forms"] == [{"block": "b2", "text": "BigQuery integráció"}]
    assert changes == ["entities BigQuery integráció: b3 → b2"]
