"""Az architektúra-teszt jelentő módban (tests/architecture.py): a tiltott importok és az idegen
táblahozzáférések számát jelenti, de nem bukik rajtuk. Amit ellenőriz: az elemzés teljes (minden
fájlnak van modulja, minden táblának gazdája), és valóban talál hozzáférést."""
import tomllib

from aaa2.contracts import CONTRACTS
from aaa2.db.connect import connect
from tests import architecture
from tests.architecture import ORDER, TABLES_FILE, summary, table_owners


def test_every_source_file_belongs_to_a_module():
    assert architecture.unmapped_files() == []
    assert len(architecture.source_files()) > 40


def test_every_table_has_an_owner_and_known_contracts():
    con = connect(":memory:")
    tables = {name for (name,) in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}
    owners = table_owners()
    assert set(owners) == tables                    # a migrációk minden táblája, és csak azok
    assert set(owners.values()) <= set(ORDER)
    names = {model.__name__ for model in CONTRACTS}
    entries = tomllib.loads(TABLES_FILE.read_text(encoding="utf-8"))["tables"]
    covered = set()
    for entry in entries.values():
        assert set(entry["contracts"]) <= names
        assert set(entry.get("also_written_by", [])) <= set(ORDER)
        covered |= set(entry["contracts"])
    assert covered == names                         # minden szerződésnek van forrástáblája


def test_the_report_counts_imports_and_table_accesses(capsys):
    info = summary()
    # az elemzés lát importot és táblahozzáférést (nem üres zöld); a számokon nem bukik
    assert info["table_accesses"] > 100
    assert info["forbidden_imports"] >= 0 and info["foreign_table_accesses"] >= 0
    # a tulajdonlás fájlja szerinti társírók azok, akik a táblát idegenként írják
    entries = tomllib.loads(TABLES_FILE.read_text(encoding="utf-8"))["tables"]
    writers: dict[str, set[str]] = {}
    for access in architecture.table_accesses():
        if access.mode == "write" and access.foreign:
            writers.setdefault(access.table, set()).add(access.module)
    assert writers == {table: set(entry["also_written_by"]) for table, entry in entries.items()
                       if entry.get("also_written_by")}
    print(architecture.report())
    assert "tiltott import" in capsys.readouterr().out


def test_the_analysis_recognises_reads_writes_and_import_direction():
    text = "INSERT INTO entities (name) SELECT name FROM pages JOIN links USING (page_id)"
    assert [m.group(1) for m in architecture.WRITE.finditer(text)] == ["entities"]
    assert [m.group(1) for m in architecture.READ.finditer(text)] == ["pages", "links"]
    deleted = architecture.DELETE_FROM.sub(" ", "DELETE FROM findings")
    assert [m.group(1) for m in architecture.READ.finditer(deleted)] == []
    assert architecture.module_of("aaa2/resolver/site.py") == "resolve"
    assert architecture.module_of("aaa2/entities/rules.py") == "extract"
    assert architecture.module_of("aaa2/engine/stable_hash.py") == "core"
    assert architecture.module_of("aaa2/engine/crawl.py") == "crawl"
    kinds = {issue.kind for issue in architecture.import_issues()}
    assert kinds <= {"upward", "cli_direct", "contracts_dependency"}
    assert "contracts_dependency" not in kinds      # a szerződések nem épülnek a motorra
    assert "upward" not in kinds                    # nincs felfelé mutató import
