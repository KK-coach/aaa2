"""Entitás-réteg: a motor (engine) által írt adatbázisból olvas, az entitás-táblákat tölti.

A motorból csak a `parse` publikus segédfüggvényeit használja (`anchor_text`, `schema_items`); a
motor nem importál innen. A két réteg a DuckDB-sémán át beszél (tests/test_boundaries.py őrzi).
"""
