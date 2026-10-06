"""Egymásba ágyazható tranzakció (`atomic`): a legkülső blokk nyit és zár, a belsők a külsőhöz
tartoznak. Így egy több lépésből álló művelet (ürítés, újraépítés) egyetlen tranzakció lehet
akkor is, ha a lépései külön is futtathatók: hiba esetén minden visszaáll, a korábbi állapot
használható marad."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb

_DEPTH: dict[int, int] = {}


@contextmanager
def atomic(con: duckdb.DuckDBPyConnection) -> Iterator[None]:
    """Tranzakció a kapcsolaton; ha már fut egy `atomic` blokk ugyanazon a kapcsolaton, annak a
    része (a véglegesítés és a visszavonás a legkülső blokké)."""
    key = id(con)
    depth = _DEPTH.get(key, 0)
    if depth == 0:
        con.begin()
    _DEPTH[key] = depth + 1
    try:
        yield
    except BaseException:
        _restore(key, depth)
        if depth == 0:
            con.rollback()
        raise
    else:
        _restore(key, depth)
        if depth == 0:
            con.commit()


def _restore(key: int, depth: int) -> None:
    if depth:
        _DEPTH[key] = depth
    else:
        _DEPTH.pop(key, None)


def inside(con: duckdb.DuckDBPyConnection) -> bool:
    """Fut-e `atomic` blokk a kapcsolaton."""
    return _DEPTH.get(id(con), 0) > 0


def begin(con: duckdb.DuckDBPyConnection) -> None:
    """Saját tranzakció nyitása, ha nem egy `atomic` blokk részeként fut a lépés."""
    if not inside(con):
        con.begin()


def commit(con: duckdb.DuckDBPyConnection) -> None:
    if not inside(con):
        con.commit()


def rollback(con: duckdb.DuckDBPyConnection) -> None:
    """A saját tranzakció visszavonása; `atomic` blokkban a visszavonás a blokké (a hívó a
    kivételt továbbadja)."""
    if not inside(con):
        con.rollback()
