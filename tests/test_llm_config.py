"""A models.toml: szolgáltatók, dátumozott árak, hosszú-kontextus sáv, a konfiguráció ellenőrzése."""
from datetime import date

import pytest

from aaa2.llm.client import LLMClient, open_clients
from aaa2.llm.config import (
    CONFIG_PATH,
    Credentials,
    PriceError,
    Usage,
    load_config,
    load_site_credentials,
)

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
        (10.0, 10.0), (10.0, 9.5), (5.0, 4.0)]
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
    assert CONFIG.pipeline == {"extraction": "gpt-6-luna", "naming": "off",
                               "verify": "gpt-6-sol"}
    config = load_config(variant(tmp_path, 'naming = "off"', 'naming = "claude-sonnet-5"'))
    assert config.pipeline == {"extraction": "gpt-6-luna", "naming": "claude-sonnet-5",
                               "verify": "gpt-6-sol"}
    assert config.provider_of(config.pipeline["naming"]) == "anthropic"
    config = load_config(variant(tmp_path, 'verify = "gpt-6-sol"', 'verify = "off"'))
    assert config.pipeline["verify"] == "off"


def test_stop_threshold_is_read_from_config(tmp_path):
    config = load_config(variant(tmp_path, "stop_usd = 9.5", "stop_usd = 2.0"))
    assert config.providers["openai"].stop_usd == 2.0


@pytest.mark.parametrize(("old", "new", "message"), [
    ("stop_usd = 9.5", "stop_usd = 10.5", r"\[openai\]: a leállási küszöb"),
    ("stop_usd = 10.0", "stop_usd = 0", r"\[anthropic\]: a leállási küszöb"),
    ('fallback = "gpt-5.6-terra"\nalternatives = ["gpt-6-sol"]\nuse_fallback = false',
     "use_fallback = true",
     r"\[openai\]: use_fallback, de nincs fallback"),
    ('model = "gpt-5.6-terra"', 'model = "gpt-5.6-terra-x"', "gpt-5.6-terra: nincs ársor"),
    ("valid_from = 2027-01-01", "valid_from = 2026-12-31", "gemini-3.8-flash: átfedő ársorok"),
    ("valid_until = 2026-12-31\n", "", "gemini-3.8-flash: átfedő ársorok"),
    ("[gemini]", "[google]", r"hiányzik a \[gemini\] szakasz"),
    ('naming = "off"', 'naming = "gpt-6-x"',
     r"\[pipeline\] naming: a gpt-6-x nincs a konfigurált modellek között"),
    ('naming = "off"', "", r"\[pipeline\]: a lépések extraction, naming, verify"),
    ('verify = "gpt-6-sol"', 'verify = "gpt-6-x"',
     r"\[pipeline\] verify: a gpt-6-x nincs a konfigurált modellek között"),
    ('extraction = "gpt-6-luna"', 'extraction = "off"',
     r"\[pipeline\] extraction: a off nincs a konfigurált modellek között"),
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


def test_site_credentials_select_the_key_and_the_openai_project(tmp_path, monkeypatch):
    """Ügyfélmunka: a site-fájl `[llm.openai]` része a saját kulcsot és projektet adja; ha a
    saját kulcs hiányzik, a szolgáltató kimarad, az alapkulcs nem lép a helyére."""
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY_X"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "ugyfel.hu.toml").write_text(
        "[llm.openai]\nkey_env = 'OPENAI_API_KEY_X'\nproject = 'proj_x'\n", encoding="utf-8")
    credentials = load_site_credentials("ugyfel.hu", tmp_path)
    assert credentials == {"openai": Credentials("OPENAI_API_KEY_X", "proj_x")}
    assert load_site_credentials("nincs.hu", tmp_path) == {}
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=alap\nOPENAI_API_KEY_X=ugyfel\n", encoding="utf-8")
    clients, _ = open_clients(None, env_file=env, credentials=credentials)
    sdk = clients["openai"].adapter.client
    assert (sdk.api_key, sdk.project) == ("ugyfel", "proj_x")
    env.write_text("OPENAI_API_KEY=alap\n", encoding="utf-8")
    clients, skipped = open_clients(None, env_file=env, credentials=credentials)
    assert "openai" not in clients and skipped["openai"] == "nincs OPENAI_API_KEY_X"


@pytest.mark.parametrize("body", ["[llm.anthropic]\nproject = 'p'\n",
                                  "[llm.openai]\nkey = 'x'\n", "[llm.openai]\nkey_env = 1\n"])
def test_site_credentials_are_validated(tmp_path, body):
    (tmp_path / "x.hu.toml").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_site_credentials("x.hu", tmp_path)
