"""Az LLM-konfiguráció (models.toml): szolgáltatók, dátumozott ártábla, hívásonkénti költség."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path

from aaa2.core.site_files import SITES_DIR

CONFIG_PATH = Path(__file__).with_name("models.toml")
PROVIDERS = ("anthropic", "openai", "gemini")


class PriceError(ValueError):
    """Nincs, vagy nem egyértelmű az ár egy modellre és napra."""


@dataclass(frozen=True)
class Usage:
    """Egy hívás tokenjei díjosztályonként. Az `output` a gondolkodási tokeneket is tartalmazza."""

    input: int
    output: int
    cached_input: int = 0
    cache_write: int = 0

    @property
    def tokens_in(self) -> int:
        return self.input + self.cached_input + self.cache_write


@dataclass(frozen=True)
class Rates:
    """USD / 1M token."""

    input: float
    cached_input: float
    output: float
    cache_write: float | None = None


@dataclass(frozen=True)
class Price:
    model: str
    rates: Rates
    source: str
    checked: date
    valid_from: date | None = None
    valid_until: date | None = None
    long_context_above: int | None = None
    long_rates: Rates | None = None

    def covers(self, day: date) -> bool:
        return (self.valid_from is None or self.valid_from <= day) and (
            self.valid_until is None or day <= self.valid_until)

    def cost_usd(self, usage: Usage) -> float:
        """A teljes kérés a hosszú-kontextus áron, ha a bemenet a küszöb fölött van."""
        long = self.long_context_above is not None and usage.tokens_in > self.long_context_above
        rates = self.long_rates if long else self.rates
        if usage.cache_write and rates.cache_write is None:
            raise PriceError(f"{self.model}: gyorsítótár-írás, de nincs rá ár")
        total = (usage.input * rates.input + usage.cached_input * rates.cached_input
                 + usage.cache_write * (rates.cache_write or 0.0) + usage.output * rates.output)
        return total / 1_000_000


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    key_env: str
    max_output_tokens: int
    budget_usd: float
    stop_usd: float
    fallback: str | None = None
    use_fallback: bool = False
    thinking_level: str | None = None
    alternatives: tuple[str, ...] = ()     # kérésre hívható további modellek (összevetéshez)

    @property
    def active_model(self) -> str:
        return self.fallback if self.use_fallback and self.fallback else self.model

    @property
    def models(self) -> tuple[str, ...]:
        """A szolgáltató konfigurált modelljei; a keret ezek együttes költségére vonatkozik."""
        return ((self.model,) + ((self.fallback,) if self.fallback else ())
                + tuple(m for m in self.alternatives if m != self.model and m != self.fallback))


@dataclass(frozen=True)
class Credentials:
    """Egy szolgáltató kulcsa és projektje egy site-hoz: `key_env` (a kulcsot tartó
    környezeti változó neve, a `models.toml` alapértéke helyett), `project` (OpenAI-projekt,
    `OpenAI-Project` fejléc)."""
    key_env: str | None = None
    project: str | None = None


# A site-fájlokból (`core/sites/<domain>.toml`) az LLM-réteg csak az `[llm]` részt olvassa.
PROJECT_PROVIDERS = ("openai",)


def load_site_credentials(domain: str | None, directory: Path | None = None
                          ) -> dict[str, Credentials]:
    """A site-fájl `[llm.<szolgáltató>]` részei: `key_env`, `project`. Ügyfélmunkánál a site a
    saját kulcsával és projektjével fut (pl. kikapcsolt adatmegosztású OpenAI-projekt). Nincs
    fájl vagy rész: üres (a `models.toml` kulcsai). Ismeretlen kulcs, projekt nem OpenAI-nál,
    vagy nem szöveg: ValueError."""
    path = (directory or SITES_DIR) / f"{domain}.toml" if domain else None
    if path is None or not path.exists():
        return {}
    section = tomllib.loads(path.read_text(encoding="utf-8")).get("llm", {})
    found: dict[str, Credentials] = {}
    for provider, values in section.items():
        unknown = set(values) - {"key_env", "project"}
        if unknown or not all(isinstance(v, str) and v for v in values.values()):
            raise ValueError(f"{path.name} [llm.{provider}]: key_env és project, szövegként")
        if values.get("project") and provider not in PROJECT_PROVIDERS:
            raise ValueError(f"{path.name} [llm.{provider}]: projekt csak az OpenAI-nál van")
        found[provider] = Credentials(values.get("key_env"), values.get("project"))
    return found


PIPELINE_STEPS = ("extraction", "verify")
PIPELINE_OFF = "off"                     # a lépés kikapcsolva (az ellenőrzésnél)
PIPELINE_OPTIONAL = ("verify",)


@dataclass(frozen=True)
class LLMConfig:
    providers: dict[str, ProviderConfig]
    prices: tuple[Price, ...]
    pipeline: dict[str, str] = field(default_factory=dict)   # lépés → modell

    def price(self, model: str, day: date) -> Price:
        matching = [p for p in self.prices if p.model == model and p.covers(day)]
        if len(matching) != 1:
            raise PriceError(f"{model}: {len(matching)} ársor érvényes {day.isoformat()}-n")
        return matching[0]

    def cost_usd(self, model: str, usage: Usage, day: date) -> float:
        return self.price(model, day).cost_usd(usage)

    def provider_of(self, model: str) -> str | None:
        return next((p.name for p in self.providers.values() if model in p.models), None)


def load_config(path: Path = CONFIG_PATH) -> LLMConfig:
    """Beolvas és ellenőriz: mindhárom szolgáltató, 0 < küszöb ≤ keret, a fallback csak
    megadott modellre kapcsolható, modellenként legalább egy ársor, egy modell ársorainak
    érvényessége nem fedi át egymást, és a `[pipeline]` minden lépésének modellje konfigurált
    (az ellenőrzésé "off" is lehet)."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    providers = {}
    for name in PROVIDERS:
        if name not in raw:
            raise ValueError(f"{path.name}: hiányzik a [{name}] szakasz")
        section = dict(raw[name])
        section["alternatives"] = tuple(section.get("alternatives", ()))
        provider = ProviderConfig(name=name, **section)
        if not 0 < provider.stop_usd <= provider.budget_usd:
            raise ValueError(f"[{name}]: a leállási küszöb 0 és a keret közé esik")
        if provider.use_fallback and not provider.fallback:
            raise ValueError(f"[{name}]: use_fallback, de nincs fallback modell")
        providers[name] = provider
    prices = tuple(_price(entry) for entry in raw.get("prices", []))
    for provider in providers.values():
        for model in provider.models:
            rows = sorted((p for p in prices if p.model == model),
                          key=lambda p: p.valid_from or date.min)
            if not rows:
                raise ValueError(f"{model}: nincs ársor")
            for earlier, later in pairwise(rows):
                if earlier.valid_until is None or later.valid_from is None \
                        or later.valid_from <= earlier.valid_until:
                    raise ValueError(f"{model}: átfedő ársorok")
    pipeline = dict(raw.get("pipeline", {}))
    if set(pipeline) != set(PIPELINE_STEPS):
        raise ValueError(f"[pipeline]: a lépések {', '.join(PIPELINE_STEPS)}")
    config = LLMConfig(providers=providers, prices=prices, pipeline=pipeline)
    for step, model in pipeline.items():
        if step in PIPELINE_OPTIONAL and model == PIPELINE_OFF:
            continue
        if config.provider_of(model) is None:
            raise ValueError(f"[pipeline] {step}: a {model} nincs a konfigurált modellek között")
    return config


def _price(entry: dict) -> Price:
    entry = dict(entry)
    long = entry.pop("long_context", None)
    fields = {k: entry.pop(k) for k in ("model", "source", "checked")}
    window = {k: entry.pop(k) for k in ("valid_from", "valid_until") if k in entry}
    price = Price(rates=Rates(**entry), **fields, **window)
    if long is None:
        return price
    long = dict(long)
    return Price(rates=price.rates, **fields, **window,
                 long_context_above=long.pop("above_input_tokens"), long_rates=Rates(**long))
