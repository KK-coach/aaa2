"""Rögzített válaszok: felvétel élő site-ról és visszajátszás a Renderer upstream-jén.

Egy felvétel egy könyvtár a `tests/fixtures/<név>/` alatt (gitignore-olt, lokálisan
felvéve): `index.json` a válaszok leírásával és a mért értékekkel, mellette a body-k.
A felvétel a `route.fetch()`-csel kéri le a választ, ami követi az átirányítást; a
visszajátszás így a végső választ adja az eredeti URL-re. Ismeretlen kérés a
visszajátszásban hálózati hibát kap, nem megy ki a hálózatra.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Request, Route

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DROPPED_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class Recording:
    def __init__(self, name: str) -> None:
        self.path = FIXTURES_DIR / name
        self.index_path = self.path / "index.json"
        self.responses: dict[str, dict] = {}
        self.measured: dict = {}
        self.misses: list[str] = []
        if self.index_path.exists():
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            self.responses = data["responses"]
            self.measured = data.get("measured", {})

    @property
    def exists(self) -> bool:
        return bool(self.responses)

    def clear(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True)
        self.responses, self.measured, self.misses = {}, {}, []

    def save(self, **measured: object) -> None:
        self.measured = measured
        self.index_path.write_text(
            json.dumps({"measured": measured, "responses": self.responses}, indent=1),
            encoding="utf-8",
        )

    async def record(self, route: Route, request: Request) -> None:
        """Renderer-upstream: élő lekérés, mentés, kiszolgálás."""
        try:
            response = await route.fetch()
            body = await response.body()
        except PlaywrightError:
            await route.abort()
            return
        headers = self._store(_key(request), response.status, response.headers, body)
        await route.fulfill(status=response.status, headers=headers, body=body)

    async def replay(self, route: Route, request: Request) -> None:
        """Renderer-upstream: a felvett válasz, vagy hálózati hiba, ha nincs felvéve."""
        entry = self._load(_key(request))
        if entry is None:
            self.misses.append(request.url)
            await route.abort("internetdisconnected")
            return
        status, headers, body = entry
        await route.fulfill(status=status, headers=headers, body=body)

    def recording_transport(self) -> httpx.AsyncBaseTransport:
        """httpx-transport: élő kérés, mentés. Az átirányítás minden lépése külön kulcs."""
        return _RecordingTransport(self)

    def replay_transport(self) -> httpx.AsyncBaseTransport:
        """httpx-transport: a felvett válasz, vagy ConnectError, ha nincs felvéve."""
        return _ReplayTransport(self)

    def _store(self, key: str, status: int, headers: dict, body: bytes) -> dict:
        kept = {k: v for k, v in headers.items() if k.lower() not in _DROPPED_HEADERS}
        name = hashlib.sha1(key.encode()).hexdigest()
        (self.path / name).write_bytes(body)
        self.responses[key] = {"status": status, "headers": kept, "body": name}
        return kept

    def _load(self, key: str) -> tuple[int, dict, bytes] | None:
        entry = self.responses.get(key)
        if entry is None:
            return None
        return entry["status"], entry["headers"], (self.path / entry["body"]).read_bytes()


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        await response.aclose()
        headers = self.recording._store(
            _http_key(request), response.status_code, dict(response.headers), body
        )
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class _ReplayTransport(httpx.AsyncBaseTransport):
    def __init__(self, recording: Recording) -> None:
        self.recording = recording

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        entry = self.recording._load(_http_key(request))
        if entry is None:
            self.recording.misses.append(str(request.url))
            raise httpx.ConnectError("nincs felvéve", request=request)
        status, headers, body = entry
        return httpx.Response(status, headers=headers, content=body, request=request)


def _key(request: Request) -> str:
    return f"{request.method} {request.url}"


def _http_key(request: httpx.Request) -> str:
    return f"httpx {request.method} {request.url}"


PAGES_DIR = FIXTURES_DIR / "pages"


def save_page(name: str, result) -> Path:
    """Egy RenderResult renderelt oldala fixture-ként: URL-ek, státusz, fejlécek, DOM."""
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = PAGES_DIR / f"{name}.json"
    path.write_text(json.dumps({
        "url": result.url,
        "final_url": result.final_url,
        "status": result.status,
        "headers": result.headers,
        "rendered_html": result.rendered_html,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def load_page(name: str) -> dict | None:
    path = PAGES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
