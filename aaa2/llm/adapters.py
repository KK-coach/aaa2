"""Szolgáltatónkénti adapterek: a kérés az API natív strukturált kimenetével megy, a válaszból a
JSON-szöveg és a díjosztályonkénti tokenszám jön vissza. A sémára validálás a kliensben van.

- Anthropic: Messages API, `output_config.format` = json_schema (`anthropic.transform_schema`).
- OpenAI: Responses API, `text_format` = a Pydantic-osztály; a tokenek és a szöveg a nyers
  válasz törzséből, így a sémának nem megfelelő, fizetett válasz is könyvelhető.
- Gemini: `response_schema` = a Pydantic-osztály, `application/json`, `thinking_level`.
"""
from __future__ import annotations

from dataclasses import dataclass

import anthropic
import httpx
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from aaa2.llm.config import ProviderConfig, Usage

# A három SDK API- és kapcsolati hibái (a google-genai a httpx kivételeit továbbengedi).
API_ERRORS = (anthropic.APIError, openai.APIError, genai_errors.APIError, httpx.HTTPError)


@dataclass(frozen=True)
class Reply:
    text: str | None                 # a JSON-kimenet; None, ha a modell nem adott szöveget
    usage: Usage
    stop: str | None = None          # a leállás oka, ha nem a rendes vég (csonka, visszautasítás)


class AnthropicAdapter:
    def __init__(self, config: ProviderConfig, api_key: str, base_url: str | None = None):
        self.config = config
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    def call(self, model: str, schema: type[BaseModel], prompt: str, input: str) -> Reply:
        message = self.client.messages.create(
            model=model,
            max_tokens=self.config.max_output_tokens,
            system=prompt,
            messages=[{"role": "user", "content": input}],
            output_config={"format": {"type": "json_schema",
                                      "schema": anthropic.transform_schema(schema)}},
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return Reply(
            text=text or None,
            usage=Usage(input=usage.input_tokens, output=usage.output_tokens,
                        cached_input=usage.cache_read_input_tokens or 0,
                        cache_write=usage.cache_creation_input_tokens or 0),
            stop=None if message.stop_reason == "end_turn" else message.stop_reason,
        )

    def list_models(self) -> list[str]:
        return [model.id for model in self.client.models.list()]


class OpenAIAdapter:
    def __init__(self, config: ProviderConfig, api_key: str, base_url: str | None = None):
        self.config = config
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def call(self, model: str, schema: type[BaseModel], prompt: str, input: str) -> Reply:
        raw = self.client.responses.with_raw_response.parse(
            model=model,
            instructions=prompt,
            input=input,
            text_format=schema,
            max_output_tokens=self.config.max_output_tokens,
            store=False,
        )
        body = raw.http_response.json()
        texts, refusal = [], None
        for item in body.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part["text"])
                elif part.get("type") == "refusal":
                    refusal = part.get("refusal")
        usage = body["usage"]
        details = usage.get("input_tokens_details") or {}
        cached = details.get("cached_tokens") or 0
        written = details.get("cache_write_tokens") or 0
        if refusal is not None:
            stop = f"refusal: {refusal}"
        elif body.get("status") != "completed":
            stop = (body.get("incomplete_details") or {}).get("reason") or body.get("status")
        else:
            stop = None
        return Reply(
            text="".join(texts) or None,
            usage=Usage(input=max(usage["input_tokens"] - cached - written, 0),
                        output=usage["output_tokens"], cached_input=cached, cache_write=written),
            stop=stop,
        )

    def list_models(self) -> list[str]:
        return [model.id for model in self.client.models.list()]


class GeminiAdapter:
    def __init__(self, config: ProviderConfig, api_key: str, base_url: str | None = None):
        self.config = config
        options = types.HttpOptions(base_url=base_url) if base_url else None
        self.client = genai.Client(api_key=api_key, vertexai=False, http_options=options)

    def call(self, model: str, schema: type[BaseModel], prompt: str, input: str) -> Reply:
        level = self.config.thinking_level
        thinking = (types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))
                    if level else None)
        response = self.client.models.generate_content(
            model=model,
            contents=input,
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=self.config.max_output_tokens,
                thinking_config=thinking,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        meta = response.usage_metadata or types.GenerateContentResponseUsageMetadata()
        cached = meta.cached_content_token_count or 0
        finish = response.candidates[0].finish_reason if response.candidates else None
        return Reply(
            text=response.text,
            usage=Usage(
                input=(meta.prompt_token_count or 0) - cached,
                output=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
                cached_input=cached),
            stop=None if finish == types.FinishReason.STOP else str(finish),
        )

    def list_models(self) -> list[str]:
        return [model.name.removeprefix("models/") for model in self.client.models.list()]


ADAPTERS = {"anthropic": AnthropicAdapter, "openai": OpenAIAdapter, "gemini": GeminiAdapter}
