"""A Natural Language API-próba jelentése (tests/acceptance/nl_probe.py), hálózat nélkül."""
import tests.acceptance.nl_probe as nl
from tests.test_gate_eval import FULL, MAIN_PAGE


def test_nl_report_matches_the_reference_and_ranks(tmp_path):
    entities = [
        {"name": "Számlázó", "type": "OTHER", "salience": 0.2, "mentions": []},
        {"name": "sebesség", "type": "OTHER", "salience": 0.5, "mentions": [
            {"text": {"content": "készletforgási sebesség"}}]},
        {"name": "Bérszámfejtés Csomag", "type": "OTHER", "salience": 0.1, "mentions": []},
    ]
    responses = {"kk_coach_meres_hu": {
        "v2": {"languageCode": "hu", "languageSupported": False, "entities": entities},
        "v1": {"language": "hu", "entities": entities}}}
    text = nl.report([MAIN_PAGE], responses, {"kk_coach_meres_hu": FULL})
    assert "languageSupported `False`" in text
    assert "1. sebesség — OTHER — 0.5000" in text
    assert "- kötelező tételek az NL-ben: 3/3" in text
    assert "Spearman -0.50" in text
    errors = {"kk_coach_meres_hu": {"v2": {"error": {"code": 403, "message": "nincs jog"}},
                                    "v1": {"error": {"code": 403, "message": "nincs jog"}}}}
    assert "- v2 hiba: 403 nincs jog" in nl.report([MAIN_PAGE], errors, {})


def test_spearman():
    assert nl.spearman(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert nl.spearman(["a", "b", "c"], ["c", "b", "a"]) == -1.0
    assert nl.spearman(["a", "b"], ["a", "b"]) is None
