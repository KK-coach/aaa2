"""Modellfüggetlen LLM-kliens: `extract(schema, prompt, input) → parsed + call_id`.

Minden visszaérkezett válasz — a sémának nem megfelelő is — sort ír a site-adatbázis
`llm_calls` táblájába és a főkönyvbe (ledger.py). Hívás előtt a keret-őr a főkönyvből összesíti
a szolgáltató modelljeinek költségét; a leállási küszöb fölött nem hív. A kulcsok a környezetből
vagy a `.env`-ből jönnek; kulcs nélkül a szolgáltató kimarad, nem hiba.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import duckdb
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError

from aaa2.llm import ledger
from aaa2.llm.adapters import ADAPTERS, API_ERRORS, Reply
from aaa2.llm.config import LLMConfig, ProviderConfig, load_config

ENV_PATH = Path(".env")


class LLMError(Exception):
    """A hívás nem adott használható eredményt."""


class BudgetExceeded(LLMError):
    """A szolgáltató halmozott költsége a leállási küszöb fölött van."""


class SchemaMismatch(LLMError):
    """A (könyvelt) válasz nem felel meg a sémának."""

    def __init__(self, message: str, call_id: int):
        super().__init__(message)
        self.call_id = call_id


class Adapter(Protocol):
    config: ProviderConfig

    def call(self, model: str, schema: type[BaseModel], prompt: str, input: str) -> Reply: ...

    def list_models(self) -> list[str]: ...


@dataclass(frozen=True)
class Extraction[T: BaseModel]:
    parsed: T
    call_id: int


class LLMClient:
    def __init__(self, con: duckdb.DuckDBPyConnection, adapter: Adapter, config: LLMConfig,
                 ledger_path: Path | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.con = con
        self.adapter = adapter
        self.config = config
        self.provider = adapter.config
        self.ledger_path = ledger_path
        self.clock = clock or _now

    @property
    def model(self) -> str:
        return self.provider.active_model

    def spent_usd(self) -> float:
        """A szolgáltató konfigurált modelljeinek halmozott költsége a főkönyvből."""
        spent = ledger.spent_by_model(self.ledger_path)
        return sum(spent.get(model, 0.0) for model in self.provider.models)

    def extract[T: BaseModel](self, schema: type[T], prompt: str, input: str, *, domain: str,
                              purpose: str = "extract", page_id: int | None = None
                              ) -> Extraction[T]:
        called_at = self.clock()
        price = self.config.price(self.model, called_at.date())
        spent = self.spent_usd()
        if spent > self.provider.stop_usd:
            raise BudgetExceeded(
                f"{self.provider.name}: {spent:.4f} USD a {self.provider.stop_usd} USD leállási "
                f"küszöb fölött (keret {self.provider.budget_usd} USD)")
        started = time.perf_counter()
        try:
            reply = self.adapter.call(self.model, schema, prompt, input)
        except API_ERRORS as exc:
            raise LLMError(f"{self.provider.name}/{self.model}: {exc}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        cost = price.cost_usd(reply.usage)
        (call_id,) = self.con.execute(
            "INSERT INTO llm_calls (domain, page_id, model, tokens_in, tokens_out, cost_usd, "
            "purpose, latency_ms, called_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING call_id",
            [domain, page_id, self.model, reply.usage.tokens_in, reply.usage.output, cost, purpose,
             latency_ms, called_at],
        ).fetchone()
        ledger.append({
            "called_at": called_at.isoformat(), "provider": self.provider.name,
            "model": self.model, "cost_usd": cost, "tokens_in": reply.usage.tokens_in,
            "tokens_out": reply.usage.output, "domain": domain, "purpose": purpose,
            "db": _database_path(self.con), "call_id": call_id,
        }, self.ledger_path)
        try:
            parsed = schema.model_validate_json(reply.text or "")
        except ValidationError as exc:
            first = exc.errors()[0]
            raise SchemaMismatch(
                f"{self.model}: a válasz nem felel meg a {schema.__name__} sémának "
                f"(call_id {call_id}, leállás: {reply.stop or 'rendes'}): "
                f"{'.'.join(map(str, first['loc'])) or 'gyökér'}: {first['msg']}",
                call_id) from exc
        return Extraction(parsed=parsed, call_id=call_id)


def api_key(name: str, env_file: Path = ENV_PATH) -> str | None:
    """A kulcs a környezetből, különben a `.env`-ből; üres érték = nincs kulcs."""
    value = os.environ.get(name)
    if not value and env_file.exists():
        value = dotenv_values(env_file).get(name)
    return value or None


def open_clients(con: duckdb.DuckDBPyConnection, *, config: LLMConfig | None = None,
                 env_file: Path = ENV_PATH, ledger_path: Path | None = None,
                 base_urls: dict[str, str] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 ) -> tuple[dict[str, LLMClient], dict[str, str]]:
    """A kulccsal rendelkező szolgáltatók kliensei, és a kimaradtak az okukkal."""
    config = config or load_config()
    clients, skipped = {}, {}
    for name, provider in config.providers.items():
        key = api_key(provider.key_env, env_file)
        if key is None:
            skipped[name] = f"nincs {provider.key_env}"
            continue
        adapter = ADAPTERS[name](provider, key, (base_urls or {}).get(name))
        clients[name] = LLMClient(con, adapter, config, ledger_path, clock)
    return clients, skipped


@dataclass(frozen=True)
class ModelCheck:
    provider: str
    model: str
    found: bool | None               # None: nem ellenőrizhető (nincs kulcs, lista-hiba)
    note: str = ""
    similar: tuple[str, ...] = ()


def check_models(*, config: LLMConfig | None = None, env_file: Path = ENV_PATH,
                 base_urls: dict[str, str] | None = None) -> list[ModelCheck]:
    """A konfigurált modell-azonosítók a szolgáltatók modell-listáján (models.list)."""
    config = config or load_config()
    checks = []
    for name, provider in config.providers.items():
        key = api_key(provider.key_env, env_file)
        if key is None:
            checks += [ModelCheck(name, model, None, f"nincs {provider.key_env}")
                       for model in provider.models]
            continue
        try:
            listed = ADAPTERS[name](provider, key, (base_urls or {}).get(name)).list_models()
        except API_ERRORS as exc:
            checks += [ModelCheck(name, model, None, f"lista-hiba: {exc}")
                       for model in provider.models]
            continue
        for model in provider.models:
            family = model.rsplit("-", 1)[0]
            similar = tuple(sorted(m for m in listed if m != model and m.startswith(family)))
            checks.append(ModelCheck(name, model, model in listed, f"{len(listed)} modell a listán",
                                     similar))
    return checks


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _database_path(con: duckdb.DuckDBPyConnection) -> str | None:
    row = con.execute(
        "SELECT path FROM duckdb_databases() WHERE database_name = current_database()").fetchone()
    return row[0] if row else None
