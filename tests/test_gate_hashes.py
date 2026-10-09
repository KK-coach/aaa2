"""A kapu hash-számítása: a futásjelentés futásfüggő sorai nem számítanak, a tartalmi sorai igen;
a rögzített alap mind a nyolc készletre megvan; a kapu parancsa. Futtatás nélkül, fájlokon."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.acceptance import gate
from tests.acceptance.determinism import differences, digests, stable_bytes

REPORT = """# Entitás-pipeline: pelda

- site: pelda.hu; oldal a készletben 14, ebből alkalmas 14
- szabálykör: #{rules}, {rules_at}
- LLM-futás: #{llm}, kinyerés modell; indult 2026-09-29 08:37, lezárva {closed}; időtartam 12.4 perc
- hívások: {calls}, költség 0.0492 USD

## Site-szintű entitások

- site-kör: #{site}; oldalszerepek: support {support}
- megjegyzés: a #12 hivatkozás és a 2026-01-01 10:00 időpont tartalom
"""
BASE = {"rules": 64, "rules_at": "2026-10-09 18:03", "llm": 7, "closed": "2026-09-29 08:50",
        "calls": 21, "site": 65, "support": 14}


def report_hash(tmp_path, name="pelda-run.md", **changed):
    path = tmp_path / name
    path.write_bytes(REPORT.format(**{**BASE, **changed}).encode())
    return hashlib.sha256(stable_bytes(path)).hexdigest()


def test_run_dependent_lines_do_not_change_the_hash(tmp_path):
    base = report_hash(tmp_path)
    assert report_hash(tmp_path, rules=66, rules_at="2026-10-10 07:15", site=67) == base
    assert report_hash(tmp_path, rules=100, rules_at="nincs lezárva") == base


@pytest.mark.parametrize("changed", [{"support": 15}, {"calls": 22}, {"llm": 8},
                                     {"closed": "2026-09-29 08:51"}])
def test_a_content_line_changes_the_hash(tmp_path, changed):
    assert report_hash(tmp_path, **changed) != report_hash(tmp_path)


def test_only_the_run_report_is_normalised_and_the_file_stays(tmp_path):
    path = tmp_path / "pelda-run.md"
    raw = REPORT.format(**BASE).encode()
    path.write_bytes(raw)
    fixed = stable_bytes(path).decode()
    assert "- szabálykör: #N, <időpont>\n" in fixed and "- site-kör: #N; oldalszerepek" in fixed
    assert "LLM-futás: #7" in fixed and "a #12 hivatkozás és a 2026-01-01 10:00" in fixed
    assert path.read_bytes() == raw
    other = tmp_path / "pelda-view-site-facts.csv"
    other.write_bytes("- szabálykör: #64, 2026-10-09 18:03\n".encode())
    assert stable_bytes(other) == other.read_bytes()
    assert report_hash(tmp_path, name="a.csv") != report_hash(tmp_path, name="a.csv", rules=66)


def test_differences_between_two_sets_of_hashes(tmp_path):
    for name, text in (("a.csv", "1"), ("b.csv", "2")):
        (tmp_path / name).write_text(text)
    one = digests(tmp_path)
    assert differences(one, dict(one)) == []
    assert differences(one, {**one, "b.csv": "x", "c.csv": "y"}) == ["b.csv", "c.csv"]
    assert differences(one, {"a.csv": one["a.csv"]}) == ["b.csv"]


def test_the_recorded_baseline_covers_the_eight_sets():
    recorded = json.loads((Path(__file__).parent / "acceptance" / "output_hashes.json")
                          .read_text(encoding="utf-8"))
    names = [name for _, name in gate.SITES]
    assert len(names) == len(set(names)) == 8
    assert sorted(recorded) == sorted(names)
    for name, files in recorded.items():
        assert f"{name}-run.md" in files and f"{name}-findings.csv" in files, name
        assert all(len(digest) == 64 for digest in files.values())


def test_gate_steps_run_the_suite_then_the_hash_on_every_set(tmp_path):
    (suite_name, suite), (hash_name, hashing) = gate.steps(tmp_path / "run", {}, False, None)
    assert (suite_name, hash_name) == ("suite", "hash")
    assert suite[:3] == [sys.executable, "-m", "pytest"] and "-m" not in suite[3:]
    assert "--write" not in hashing and "--hashes" in hashing
    assert [a for a in hashing if "=" in a] == [
        f"{domain}={Path('data') / 'm3' / (name + '.duckdb')}" for domain, name in gate.SITES]
    _, updating = gate.steps(tmp_path / "run", {"duex": "mashol/duex.duckdb"}, True, "4")
    assert "--write" in updating[1] and updating[1][updating[1].index("--jobs") + 1] == "4"
    assert "duexhungary.hu=mashol/duex.duckdb" in updating[1]
    with pytest.raises(SystemExit):
        gate.steps(tmp_path / "run", {"nincs-ilyen": "x.duckdb"}, False, None)


def test_a_failed_suite_never_updates_the_baseline(tmp_path):
    def hashing(update, suite_code):
        return gate.steps(tmp_path / "run", {}, update, None, suite_code)[1][1]
    assert "--write" in hashing(True, 0)
    for code in (1, 2, -1):
        failed = hashing(True, code)
        assert "--write" not in failed
        assert "--hashes" in failed and len([a for a in failed if "=" in a]) == 8   # a lépés fut
    assert "--write" not in hashing(False, 0)


def test_the_gate_writes_the_reason_when_the_suite_fails(tmp_path, monkeypatch):
    calls = []

    class Done:
        def __init__(self, code, stdout=""):
            self.returncode, self.stdout = code, stdout

    def run(command, **kwargs):
        if command[0] == "git":
            return Done(0, "abc1234\n")
        calls.append(command)
        kwargs["stdout"].write("kimenet\n")
        return Done(1 if "pytest" in command else 0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    run_dir = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["gate", str(run_dir), "--update-hashes"])
    with pytest.raises(SystemExit) as stopped:
        gate.main()
    assert stopped.value.code == 1
    assert "pytest" in calls[0] and "--write" not in calls[1] and "--hashes" in calls[1]
    done = (run_dir / "done.txt").read_text(encoding="utf-8").splitlines()
    assert done[0].startswith("suite: kilépési kód 1") and done[1] == gate.NOT_UPDATED
    assert done[2].startswith("hash: kilépési kód 0") and done[-1].endswith("ELTÉRÉS VAGY HIBA")

    calls.clear()
    monkeypatch.setattr(gate.subprocess, "run", lambda command, **kwargs: (
        Done(0, "abc1234\n") if command[0] == "git"
        else (calls.append(command), kwargs["stdout"].write("ok\n"), Done(0))[2]))
    green = tmp_path / "green"
    monkeypatch.setattr(sys, "argv", ["gate", str(green), "--update-hashes"])
    with pytest.raises(SystemExit) as stopped:
        gate.main()
    assert stopped.value.code == 0 and "--write" in calls[1]
    assert gate.NOT_UPDATED not in (green / "done.txt").read_text(encoding="utf-8")
