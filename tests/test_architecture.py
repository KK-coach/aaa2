"""Az architektúra-teszt szigorú módban (tests/architecture.py): bukik, ha tiltott import
(felfelé mutató, a CLI nem az `api`-n át ér el egy modult, vagy a szerződések a motorra épülnek)
vagy idegen táblahozzáférés jelenik meg, vagy ha az entitástár tábláihoz a táron kívül áll SQL.
Azt is ellenőrzi, hogy az elemzés teljes (minden fájlnak van modulja, minden táblának gazdája)."""
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
    # nincs tiltott import: a CLI csak az `api`-t használja, felfelé mutató import nincs
    assert [(i.kind, i.file, i.imported) for i in architecture.import_issues()] == []
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
    assert architecture.module_of("aaa2/api/steps.py") == "api"
    assert architecture.module_of("aaa2/cli/main.py") == "cli"
    assert architecture.import_issues() == []
    # a CLI egyetlen aaa2-importja az api
    cli_source = (architecture.ROOT / "aaa2" / "cli" / "main.py").read_text(encoding="utf-8")
    assert [line for line in cli_source.splitlines()
            if line.startswith(("from aaa2", "import aaa2"))] == ["from aaa2 import api"]
