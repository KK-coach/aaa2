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
        """Upstream: élő lekérés, mentés, kiszolgálás."""
        try:
            response = await route.fetch()
            body = await response.body()
        except PlaywrightError:
            await route.abort()
            return
        headers = {k: v for k, v in response.headers.items() if k.lower() not in _DROPPED_HEADERS}
        key = _key(request)
        name = hashlib.sha1(key.encode()).hexdigest()
        (self.path / name).write_bytes(body)
        self.responses[key] = {"status": response.status, "headers": headers, "body": name}
        await route.fulfill(status=response.status, headers=headers, body=body)

    async def replay(self, route: Route, request: Request) -> None:
        """Upstream: a felvett válasz, vagy hálózati hiba, ha nincs felvéve."""
        entry = self.responses.get(_key(request))
        if entry is None:
            self.misses.append(request.url)
            await route.abort("internetdisconnected")
            return
        body = (self.path / entry["body"]).read_bytes()
        await route.fulfill(status=entry["status"], headers=entry["headers"], body=body)


def _key(request: Request) -> str:
    return f"{request.method} {request.url}"
