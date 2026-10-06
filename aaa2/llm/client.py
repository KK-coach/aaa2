"""Modellfüggetlen LLM-kliens: `extract(schema, prompt, input) → parsed + call_id`.

Minden visszaérkezett válasz — a sémának nem megfelelő is — sort ír a site-adatbázis
`llm_calls` táblájába és a főkönyvbe (ledger.py). Hívás előtt a keret-őr a főkönyvből összesíti
a szolgáltató modelljeinek költségét, és hozzáadja a hívás legnagyobb költségét (a bemenet
konzervatív becslése és a teljes `max_output_tokens`, vagy a hívás saját, kisebb
`max_output_tokens`-e, ha megadja); ha ez a leállási küszöb fölé vinné, nem hív. Párhuzamos
hívásoknál a még futó hívások legnagyobb költsége is beszámít (`_RESERVED`, zár alatt), így a
szálak együtt sem lépik át a küszöböt. Átmeneti hibánál (408,
429, 5xx, kapcsolat) exponenciális várakozással és jitterrel újrapróbál; a kísérletek száma az
`llm_calls.attempts`-be, az utolsó sikertelen kísérlet hibája a `last_error`-ba kerül. A kulcsok
a környezetből vagy a `.env`-ből jönnek; kulcs nélkül a szolgáltató kimarad, nem hiba.
"""
from __future__ import annotations

import copy
import os
import random
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import duckdb
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError

from aaa2.llm import ledger
from aaa2.llm.adapters import ADAPTERS, API_ERRORS, Reply, describe_error, is_transient
from aaa2.llm.config import Credentials, LLMConfig, ProviderConfig, Usage, load_config

ENV_PATH = Path(".env")
# A költségőr bemenet-becslése: ennyi karakter egy token (konzervatív; a magyar szöveg ~3).
CHARS_PER_TOKEN = 2
# Szolgáltatónként a még futó hívások legnagyobb költsége (a párhuzamos szálak közös kerete).
_RESERVED: dict[str, float] = {}
_RESERVE_LOCK = threading.Lock()


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

    def call(self, model: str, schema: type[BaseModel], prompt: str, input: str,
             max_output_tokens: int | None = None) -> Reply: ...

    def list_models(self) -> list[str]: ...


@dataclass(frozen=True)
class Extraction[T: BaseModel]:
    parsed: T
    call_id: int


@dataclass(frozen=True)
class Retry:
    """Az n. újrapróba előtt `delays[n] × U(1 − jitter, 1 + jitter)` másodperc várakozás; a
    kísérletek száma legfeljebb 1 + len(delays)."""

    delays: tuple[float, ...] = (1.0, 3.0, 9.0)
    jitter: float = 0.25
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def wait(self, retry: int) -> None:
        self.sleep(self.delays[retry] * self.rng.uniform(1 - self.jitter, 1 + self.jitter))


class LLMClient:
    def __init__(self, con: duckdb.DuckDBPyConnection, adapter: Adapter, config: LLMConfig,
                 ledger_path: Path | None = None,
                 clock: Callable[[], datetime] | None = None, retry: Retry | None = None,
                 model: str | None = None):
        """`model`: a szolgáltató egy konfigurált modellje (`ProviderConfig.models`) az aktív
        helyett."""
        self.con = con
        self.adapter = adapter
        self.config = config
        self.provider = adapter.config
        if model is not None and model not in self.provider.models:
            raise ValueError(f"{self.provider.name}: a {model} nincs a konfigurált modellek "
                             f"között ({', '.join(self.provider.models)})")
        self._model = model
        self.ledger_path = ledger_path
        self.clock = clock or _now
        self.retry = retry or Retry()

    @property
    def model(self) -> str:
        return self._model or self.provider.active_model

    def bind(self, con: duckdb.DuckDBPyConnection) -> LLMClient:
        """Ugyanez a kliens egy másik kapcsolattal (a párhuzamos szál saját kurzorával: a
        DuckDB-kapcsolat nem szálbiztos); az adapter és a keret közös."""
        bound = copy.copy(self)
        bound.con = con
        return bound

    def spent_usd(self) -> float:
        """A szolgáltató konfigurált modelljeinek halmozott költsége a főkönyvből."""
        spent = ledger.spent_by_model(self.ledger_path)
        return sum(spent.get(model, 0.0) for model in self.provider.models)

    def output_limit(self, max_output_tokens: int | None = None) -> int:
        """A hívás kimeneti plafonja: a megadott, de legfeljebb a szolgáltatóé."""
        limit = self.provider.max_output_tokens
        return min(max_output_tokens, limit) if max_output_tokens else limit

    def worst_case_usd(self, prompt: str, input: str, day,
                       max_output_tokens: int | None = None) -> float:
        """Egy hívás legnagyobb költsége: a bemenet konzervatív tokenbecslése
        (`CHARS_PER_TOKEN` karakterenként egy token) és a kimeneti plafon (`output_limit`) a
        modell árán, a hosszú-kontextus sávval együtt."""
        tokens_in = -(-(len(prompt) + len(input)) // CHARS_PER_TOKEN)
        usage = Usage(input=tokens_in, output=self.output_limit(max_output_tokens))
        return self.config.price(self.model, day).cost_usd(usage)

    def extract[T: BaseModel](self, schema: type[T], prompt: str, input: str, *, domain: str,
                              purpose: str = "extract", page_id: int | None = None,
                              max_output_tokens: int | None = None) -> Extraction[T]:
        """`max_output_tokens`: a hívás saját kimeneti plafonja (legfeljebb a szolgáltatóé);
        a költségőr is ezzel számol."""
        called_at = self.clock()
        price = self.config.price(self.model, called_at.date())
        limit = self.output_limit(max_output_tokens)
        worst = self.worst_case_usd(prompt, input, called_at.date(), limit)
        name = self.provider.name
        with _RESERVE_LOCK:
            spent = self.spent_usd() + _RESERVED.get(name, 0.0)
            if spent + worst > self.provider.stop_usd:
                raise BudgetExceeded(
                    f"{name}: {spent:.4f} USD + ez a hívás legfeljebb {worst:.4f} USD "
                    f"(max_output_tokens {limit}) a "
                    f"{self.provider.stop_usd} USD leállási küszöb fölé vinné "
                    f"(keret {self.provider.budget_usd} USD)")
            _RESERVED[name] = _RESERVED.get(name, 0.0) + worst
        try:
            reply, attempts, latency_ms, last_error = self._call(
                schema, prompt, input, max_output_tokens if max_output_tokens else None)
            cost = price.cost_usd(reply.usage)
            (call_id,) = self.con.execute(
                "INSERT INTO llm_calls (domain, page_id, model, tokens_in, tokens_out, cost_usd, "
                "purpose, latency_ms, called_at, attempts, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING call_id",
                [domain, page_id, self.model, reply.usage.tokens_in, reply.usage.output, cost,
                 purpose, latency_ms, called_at, attempts, last_error],
            ).fetchone()
            ledger.append({
                "called_at": called_at.isoformat(), "provider": name,
                "model": self.model, "cost_usd": cost, "tokens_in": reply.usage.tokens_in,
                "tokens_out": reply.usage.output, "attempts": attempts,
                "last_error": last_error, "domain": domain, "purpose": purpose,
                "db": _database_path(self.con), "call_id": call_id, "usage": reply.raw_usage,
            }, self.ledger_path)
        finally:
            with _RESERVE_LOCK:
                _RESERVED[name] = max(0.0, _RESERVED.get(name, 0.0) - worst)
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

    def _call(self, schema: type[BaseModel], prompt: str, input: str,
              max_output_tokens: int | None = None) -> tuple[Reply, int, int, str | None]:
        """A válasz, a kísérletek száma, a sikeres kísérlet késleltetése (ms) és az utolsó
        sikertelen kísérlet hibája (None, ha nem kellett újrapróba)."""
        attempt, last_error = 0, None
        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                extra = ({"max_output_tokens": self.output_limit(max_output_tokens)}
                         if max_output_tokens else {})
                reply = self.adapter.call(self.model, schema, prompt, input, **extra)
            except API_ERRORS as exc:
                if attempt > len(self.retry.delays) or not is_transient(exc):
                    tries = f"{attempt} kísérlet után: " if attempt > 1 else ""
                    raise LLMError(f"{self.provider.name}/{self.model}: {tries}{exc}") from exc
                last_error = describe_error(exc)
                self.retry.wait(attempt - 1)
                continue
            return reply, attempt, round((time.perf_counter() - started) * 1000), last_error


def api_key(name: str, env_file: Path = ENV_PATH) -> str | None:
    """A kulcs a környezetből, különben a `.env`-ből; üres érték = nincs kulcs."""
    value = os.environ.get(name)
    if not value and env_file.exists():
        value = dotenv_values(env_file).get(name)
    return value or None


def open_clients(con: duckdb.DuckDBPyConnection, *, config: LLMConfig | None = None,
                 env_file: Path = ENV_PATH, ledger_path: Path | None = None,
                 base_urls: dict[str, str] | None = None,
                 clock: Callable[[], datetime] | None = None, retry: Retry | None = None,
                 models: dict[str, str] | None = None,
                 credentials: Mapping[str, Credentials] | None = None,
                 ) -> tuple[dict[str, LLMClient], dict[str, str]]:
    """A kulccsal rendelkező szolgáltatók kliensei, és a kimaradtak az okukkal. `models`:
    szolgáltatónként egy konfigurált modell az aktív helyett. `credentials`: a site saját
    kulcsa és projektje (`config.load_site_credentials`); ha a site saját kulcsot ad meg, és az
    hiányzik, a szolgáltató kimarad, az alapkulcs nem lép a helyére."""
    config = config or load_config()
    clients, skipped = {}, {}
    for name, provider in config.providers.items():
        own = (credentials or {}).get(name, Credentials())
        key_env = own.key_env or provider.key_env
        key = api_key(key_env, env_file)
        if key is None:
            skipped[name] = f"nincs {key_env}"
            continue
        extra = {"project": own.project} if own.project else {}
        adapter = ADAPTERS[name](provider, key, (base_urls or {}).get(name), **extra)
        clients[name] = LLMClient(con, adapter, config, ledger_path, clock, retry,
                                  (models or {}).get(name))
    return clients, skipped


def key_sources(config: LLMConfig, credentials: Mapping[str, Credentials] | None,
                providers: Iterable[str]) -> list[str]:
    """Soronként, melyik kulcskészlettel fut egy szolgáltató: a kulcsot tartó környezeti változó
    neve (az értéke soha), és hogy az a site-fájl saját kulcsa-e vagy az alapkulcs; a projekt,
    ha van. A futás elején írjuk ki, hogy az ügyfélkulcs használata látható legyen."""
    lines = []
    for name in sorted(set(providers)):
        own = (credentials or {}).get(name, Credentials())
        origin = "a site-fájl kulcsa" if own.key_env else "alapkulcs"
        lines.append(f"LLM-kulcs: {name}: {own.key_env or config.providers[name].key_env} "
                     f"({origin})" + (f", projekt {own.project}" if own.project else ""))
    return lines


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
