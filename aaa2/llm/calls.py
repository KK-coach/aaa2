"""Az `llm_calls` tábla lekérdezései és módosítása más moduloknak: a tábla az `llm` modulé
(a sorokat a kliens írja, `client.py`), más modul közvetlen SQL nélkül ezeken át éri el."""
from __future__ import annotations

from collections.abc import Iterable

import duckdb

from aaa2.contracts import LLMCall


def calls(con: duckdb.DuckDBPyConnection) -> list[LLMCall]:
    """Minden hívás, `call_id` szerint."""
    cursor = con.execute("SELECT * FROM llm_calls ORDER BY call_id")
    names = [column[0] for column in cursor.description]
    return [LLMCall.from_row(dict(zip(names, row, strict=True))) for row in cursor.fetchall()]


def model_call_ids(con: duckdb.DuckDBPyConnection, model: str) -> list[int]:
    """A modell hívásainak azonosítói."""
    return [call_id for (call_id,) in con.execute(
        "SELECT call_id FROM llm_calls WHERE model = ? ORDER BY call_id", [model]).fetchall()]


def total_cost(con: duckdb.DuckDBPyConnection, call_ids: Iterable[int]) -> float:
    """A megadott hívások költsége (USD)."""
    return con.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls "
                       "WHERE list_contains(?, call_id)", [sorted(set(call_ids))]).fetchone()[0]


def usage_by_purpose(con: duckdb.DuckDBPyConnection, call_ids: Iterable[int]) -> list[tuple]:
    """A megadott hívások cél és modell szerint: (cél, modell, hívás, token be, token ki,
    költség, újrapróba, hibás válasz)."""
    return con.execute(
        "SELECT purpose, model, count(*), sum(tokens_in), sum(tokens_out), sum(cost_usd), "
        "sum(coalesce(attempts, 1) - 1), count(*) FILTER (WHERE last_error IS NOT NULL) "
        "FROM llm_calls WHERE list_contains(?, call_id) GROUP BY purpose, model "
        "ORDER BY purpose, model", [sorted(set(call_ids))]).fetchall()


def spend_by_model(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Modellenként: (modell, hívás, költség, újrapróba, a hibakódok felsorolva)."""
    return con.execute(
        "SELECT model, count(*), sum(cost_usd), sum(coalesce(attempts, 1) - 1), "
        "array_to_string(list_sort(list(DISTINCT split_part(last_error, ':', 1)) "
        "FILTER (WHERE last_error IS NOT NULL)), ', ') "
        "FROM llm_calls GROUP BY model ORDER BY model").fetchall()


def set_fabricated_count(con: duckdb.DuckDBPyConnection, call_id: int, count: int) -> None:
    """A hívás kitalált említéseinek száma (a kinyerés fabrikáció-szűrője állapítja meg)."""
    con.execute("UPDATE llm_calls SET fabricated_count = ? WHERE call_id = ?", [count, call_id])
