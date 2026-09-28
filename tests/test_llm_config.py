"""A models.toml: szolgáltatók, dátumozott árak, hosszú-kontextus sáv, a konfiguráció ellenőrzése."""
from datetime import date

import pytest

from aaa2.llm.client import LLMClient, open_clients
from aaa2.llm.config import CONFIG_PATH, PriceError, Usage, load_config

CONFIG = load_config()
TEXT = CONFIG_PATH.read_text(encoding="utf-8")


def variant(tmp_path, old, new):
    assert old in TEXT
    path = tmp_path / "models.toml"
    path.write_text(TEXT.replace(old, new, 1), encoding="utf-8")
    return path


def test_defaults():
    providers = CONFIG.providers
    assert [providers[n].active_model for n in ("anthropic", "openai", "gemini")] == [
        "claude-opus-5-5", "gpt-6-luna", "gemini-3.8-flash"]
    assert providers["openai"].models == ("gpt-6-luna", "gpt-5.6-terra", "gpt-6-sol")
    assert providers["anthropic"].models == (
        "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001")
    assert providers["gemini"].thinking_level == "low"
    assert [(p.budget_usd, p.stop_usd) for p in providers.values()] == [
        (10.0, 10.0), (3.0, 2.5), (5.0, 4.0)]
    assert "gpt-6-astra" not in {p.model for p in CONFIG.prices}
    assert [p.max_output_tokens for p in providers.values()] == [16000, 32000, 16000]


def test_gemini_introductory_price_ends_on_2026_12_31():
    usage = Usage(input=1_000_000, output=1_000_000)
    assert CONFIG.cost_usd("gemini-3.8-flash", usage, date(2026, 12, 31)) == pytest.approx(4.50)
    assert CONFIG.cost_usd("gemini-3.8-flash", usage, date(2027, 1, 1)) == pytest.approx(9.00)
    cached = Usage(input=0, output=0, cached_input=1_000_000)
    assert CONFIG.cost_usd("gemini-3.8-flash", cached, date(2026, 9, 26)) == pytest.approx(0.075)
    assert CONFIG.cost_usd("gemini-3.8-flash", cached, date(2027, 1, 1)) == pytest.approx(0.15)


def test_openai_long_context_prices_the_whole_request():
    day = date(2026, 9, 26)
    short = Usage(input=262_000, cached_input=10_000, output=10_000)      # 272 000: még rövid
    long = Usage(input=262_001, cached_input=10_000, output=10_000)       # 272 001: hosszú
    assert CONFIG.cost_usd("gpt-6-luna", short, day) == pytest.approx(
        (262_000 * 0.10 + 10_000 * 0.01 + 10_000 * 0.50) / 1e6)
    assert CONFIG.cost_usd("gpt-6-luna", long, day) == pytest.approx(
        (262_001 * 0.20 + 10_000 * 0.02 + 10_000 * 0.75) / 1e6)
    assert CONFIG.cost_usd("gpt-5.6-terra", long, day) == pytest.approx(
        (262_001 * 4.00 + 10_000 * 0.40 + 10_000 * 18.00) / 1e6)


def test_anthropic_cache_classes():
    usage = Usage(input=100_000, output=10_000, cached_input=200_000, cache_write=50_000)
    assert CONFIG.cost_usd("claude-opus-5-5", usage, date(2026, 9, 26)) == pytest.approx(
        (100_000 * 4 + 200_000 * 0.20 + 50_000 * 5 + 10_000 * 20) / 1e6)


def test_cache_write_without_a_price_is_refused():
    with pytest.raises(PriceError, match="gyorsítótár-írás"):
        CONFIG.cost_usd("gemini-3.8-flash", Usage(input=1, output=1, cache_write=1),
                        date(2026, 9, 26))


def test_unknown_model_or_day_has_no_price():
    with pytest.raises(PriceError, match="gpt-6-astra: 0 ársor"):
        CONFIG.price("gpt-6-astra", date(2026, 9, 26))


def test_fallback_is_switched_from_config(tmp_path):
    config = load_config(variant(tmp_path, "use_fallback = false", "use_fallback = true"))
    assert config.providers["openai"].active_model == "gpt-5.6-terra"
    assert config.provider_of("gpt-5.6-terra") == "openai"
    assert config.price("gpt-5.6-terra", date(2026, 9, 26)).rates.output == 12.00


def test_pipeline_steps_choose_their_models_independently(tmp_path):
    assert CONFIG.pipeline == {"extraction": "gpt-6-luna", "naming": "gpt-6-luna"}
    config = load_config(variant(tmp_path, 'naming = "gpt-6-luna"',
                                 'naming = "claude-sonnet-5"'))
    assert config.pipeline == {"extraction": "gpt-6-luna", "naming": "claude-sonnet-5"}
    assert config.provider_of(config.pipeline["naming"]) == "anthropic"


def test_stop_threshold_is_read_from_config(tmp_path):
    config = load_config(variant(tmp_path, "stop_usd = 2.5", "stop_usd = 2.0"))
    assert config.providers["openai"].stop_usd == 2.0


@pytest.mark.parametrize(("old", "new", "message"), [
    ("stop_usd = 2.5", "stop_usd = 3.5", r"\[openai\]: a leállási küszöb"),
    ("stop_usd = 10.0", "stop_usd = 0", r"\[anthropic\]: a leállási küszöb"),
    ('fallback = "gpt-5.6-terra"\nalternatives = ["gpt-6-sol"]\nuse_fallback = false',
     "use_fallback = true",
     r"\[openai\]: use_fallback, de nincs fallback"),
    ('model = "gpt-5.6-terra"', 'model = "gpt-5.6-terra-x"', "gpt-5.6-terra: nincs ársor"),
    ("valid_from = 2027-01-01", "valid_from = 2026-12-31", "gemini-3.8-flash: átfedő ársorok"),
    ("valid_until = 2026-12-31\n", "", "gemini-3.8-flash: átfedő ársorok"),
    ("[gemini]", "[google]", r"hiányzik a \[gemini\] szakasz"),
    ('naming = "gpt-6-luna"', 'naming = "gpt-6-x"',
     r"\[pipeline\] naming: a gpt-6-x nincs a konfigurált modellek között"),
    ('naming = "gpt-6-luna"', "", r"\[pipeline\]: a lépések extraction, naming"),
    ('alternatives = ["gpt-6-sol"]', 'alternatives = ["gpt-6-sol", "gpt-6-x"]',
     "gpt-6-x: nincs ársor"),
])
def test_invalid_config_is_refused(tmp_path, old, new, message):
    with pytest.raises(ValueError, match=message):
        load_config(variant(tmp_path, old, new))


def test_every_price_row_names_its_source():
    assert all(p.source.startswith("https://")
               and p.checked in (date(2026, 9, 26), date(2026, 9, 27)) for p in CONFIG.prices)


@pytest.mark.parametrize(("model", "rates"), [
    ("gpt-6-sol", (2.00, 0.20, 2.50, 10.00)),
    ("claude-sonnet-5", (2.00, 0.20, 2.50, 10.00)),
    ("claude-haiku-4-5-20251001", (1.00, 0.10, 1.25, 5.00)),
])
def test_candidate_prices(model, rates):
    r = CONFIG.price(model, date(2026, 9, 27)).rates
    assert (r.input, r.cached_input, r.cache_write, r.output) == rates


class _Adapter:
    def __init__(self, provider):
        self.config = CONFIG.providers[provider]


def test_client_model_override_is_a_configured_model():
    assert LLMClient(None, _Adapter("openai"), CONFIG).model == "gpt-6-luna"
    assert LLMClient(None, _Adapter("openai"), CONFIG, model="gpt-6-sol").model == "gpt-6-sol"
    with pytest.raises(ValueError, match="gpt-6-astra nincs a konfigurált modellek"):
        LLMClient(None, _Adapter("openai"), CONFIG, model="gpt-6-astra")


def test_open_clients_takes_a_model_per_provider(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=a\nOPENAI_API_KEY=o\n", encoding="utf-8")
    clients, skipped = open_clients(None, env_file=env,
                                    models={"anthropic": "claude-sonnet-5"})
    assert (clients["anthropic"].model, clients["openai"].model) == (
        "claude-sonnet-5", "gpt-6-luna")
    assert "gemini" in skipped
