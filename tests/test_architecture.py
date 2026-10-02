"""Az architektúra-teszt (tests/architecture.py). A tiltott importok közül a CLI közvetlen
importjait még csak jelenti (az `api` homlokzatig); bukik, ha felfelé mutató import vagy idegen
táblahozzáférés jelenik meg, vagy ha az entitástár tábláihoz a táron kívül áll SQL. Azt is
ellenőrzi, hogy az elemzés teljes (minden fájlnak van modulja, minden táblának gazdája)."""
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
        covered |= set(entry["contracts"])
    assert covered == names                         # minden szerződésnek van forrástáblája


def test_the_report_counts_imports_and_table_accesses(capsys):
    info = summary()
    # az elemzés lát importot és táblahozzáférést (nem üres zöld)
    assert info["table_accesses"] > 100
    assert info["forbidden_imports"] >= 0
    # egy modul sem ír vagy olvas közvetlen SQL-lel más modul tábláját
    assert [(a.file, a.line, a.table, a.mode) for a in architecture.table_accesses()
            if a.foreign] == []
    # az entitástár tábláihoz SQL csak a tár fájljában áll
    entries = tomllib.loads(TABLES_FILE.read_text(encoding="utf-8"))["tables"]
    store_tables = {table for table, entry in entries.items() if entry.get("store")}
    assert store_tables == {"entities", "page_entities", "mention_sources", "soft_checks",
                            "entity_runs"}
    assert sorted({a.file for a in architecture.table_accesses() if a.table in store_tables}) \
        == ["aaa2/entities/store.py"]
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
