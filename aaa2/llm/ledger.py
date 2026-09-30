"""Az LLM-hívások főkönyve: data/llm_ledger.jsonl, soronként egy hívás, minden site-ról.

A keret-őr és az `aaa status` modellenkénti halmozott költsége ebből számol. Az `llm_calls` sor a
site-adatbázisban marad (page_id, mention_sources.llm_call_id); a főkönyv sora hivatkozik rá
(db, call_id). Az írás és az olvasás szálbiztos (`LOCK`): a párhuzamos kinyerés szálai egy
főkönyvbe írnak.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

import aaa2.db.connect as connect_module

LOCK = threading.RLock()


def default_path() -> Path:
    return connect_module.DATA_DIR / "llm_ledger.jsonl"


def append(entry: dict, path: Path | None = None) -> None:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def spent_by_model(path: Path | None = None) -> dict[str, float]:
    """Modellenként a halmozott USD; üres, ha még nincs főkönyv."""
    path = path or default_path()
    totals: dict[str, float] = defaultdict(float)
    with LOCK:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text:
        for line in text.splitlines():
            if line.strip():
                entry = json.loads(line)
                totals[entry["model"]] += entry["cost_usd"]
    return dict(totals)
