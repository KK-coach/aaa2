"""A site-kör futásának állapota (`SiteRun`) és közös környezete (`_Context`)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import duckdb
import zstandard

from aaa2.entities.dom import parse_blocks
from aaa2.entities.placeholder import placeholder_pages
from aaa2.resolver.names import _site_name_keys
from aaa2.resolver.pages import (
    PageInfo,
    entity_groups,
)


@dataclass
class SiteRun:
    run_id: int
    roles: dict[str, int]
    page_entities: int = 0
    packages: int = 0
    steps: int = 0
    merges: Counter[str] = field(default_factory=Counter)
    anchor_mentions: int = 0
    offers: int = 0
    overrides: int = 0
    shop: dict = field(default_factory=dict)
    demo: list[str] = field(default_factory=list)
    placeholder_pages: list[str] = field(default_factory=list)
    template_mentions: int = 0
    template_entities: int = 0
    thresholds: dict[str, float] = field(default_factory=dict)


class _Context:
    def __init__(self, con: duckdb.DuckDBPyConnection, roles: dict[int, PageInfo],
                 site_lang: str | None, run_id: int):
        self.con, self.roles, self.site_lang, self.run_id = con, roles, site_lang, run_id
        self.placeholder = placeholder_pages(con)
        self.groups = {group: members for group, members in entity_groups(roles).items()
                       if not all(m.page_id in self.placeholder for m in members)}
        self.site_keys = _site_name_keys(con)
        self._dom: dict[int, list] = {}

    def dom(self, page_id: int) -> list:
        if page_id not in self._dom:
            title, blob = self.con.execute(
                "SELECT title, rendered_html FROM pages WHERE page_id = ? ORDER BY ALL", [page_id]).fetchone()
            html = zstandard.ZstdDecompressor().decompress(blob).decode("utf-8", "replace")
            self._dom[page_id] = parse_blocks(html, title)
        return self._dom[page_id]

    def block_id(self, page_id: int, ordinal: int) -> int | None:
        row = self.con.execute("SELECT block_id FROM blocks WHERE page_id = ? AND ordinal = ? ORDER BY ALL",
                               [page_id, ordinal]).fetchone()
        return row[0] if row else None
