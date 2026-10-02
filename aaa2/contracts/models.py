"""A modulok kimeneti szerződései: típusos, verziózott adatmodellek (architektúra-spec, 2. és
4. pont).

- Modulonként: `llm` → `LLMCall`; `crawl` → `Page`, `Link`, `PageMeta`, `StructuredData`; `extract` → `Block`,
  `Mention`, `Candidate`; `resolve` → `Entity`, `Alias`, `Relation`, `MergeRecord`, `KbLink`;
  `graph` → `PageNode`, `Edge`, `MainEntity`, `EntityWeight`; `findings` → `Finding`.
- Minden modell `schema_version` mezőt hordoz (`SCHEMA_VERSION`); a szerződés változása
  verzióemelés.
- `from_row`: a modell egy mai adatbázissorból (oszlopnév → érték) épül fel; a sor többi oszlopa
  kimarad, a JSON-oszlop szövege objektummá válik. A modellek csak adatot írnak le: adatbázist
  nem érnek el, és a csomag a motor egyik moduljára sem épül.
- `StructuredData`: a JSON-LD (`schema_blocks`), a microdata és az RDFa (`structured_data`),
  később az Open Graph közös alakja.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"

NodeKind = Literal["page", "entity"]
EdgeType = Literal["mentions", "main_entity", "part_of", "brand_of", "offers", "is_a", "about",
                   "duplicate_of", "supports"]
Severity = Literal["high", "medium", "low"]
Confidence = Literal["strong", "medium", "weak"]
StructuredSyntax = Literal["json-ld", "microdata", "rdfa", "opengraph"]


class Contract(BaseModel):
    """A szerződések közös őse: változtathatatlan, ismeretlen mezőt nem fogad."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    module: ClassVar[str] = ""                  # a szerződést kiadó modul
    renamed: ClassVar[dict[str, str]] = {}      # adatbázis-oszlop → mező, ahol a név eltér
    schema_version: str = SCHEMA_VERSION

    @field_validator("*", mode="before")
    @classmethod
    def _json_text(cls, value: Any, info: Any) -> Any:
        """A JSON-oszlop szövegként érkezik az adatbázisból."""
        if isinstance(value, str) and info.field_name in cls.json_fields():
            return json.loads(value)
        return value

    @classmethod
    def json_fields(cls) -> frozenset[str]:
        return frozenset(name for name, field in cls.model_fields.items()
                         if (field.json_schema_extra or {}).get("json_column"))

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Self:
        named = {cls.renamed.get(column, column): value for column, value in row.items()}
        return cls(**{name: named[name] for name in cls.model_fields if name in named})


def _json(default: Any = None, **kwargs: Any) -> Any:
    return Field(default, json_schema_extra={"json_column": True}, **kwargs)


# --- llm -----------------------------------------------------------------------------------

class LLMCall(Contract):
    """Egy modellhívás a költségével (`llm_calls`)."""

    module: ClassVar[str] = "llm"
    call_id: int
    domain: str
    page_id: int | None = None
    model: str
    purpose: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    attempts: int | None = None
    last_error: str | None = None
    fabricated_count: int | None = None
    called_at: datetime


# --- crawl ---------------------------------------------------------------------------------

class Page(Contract):
    """Egy bejárt oldal (`pages`), a renderelt HTML nélkül."""

    module: ClassVar[str] = "crawl"
    page_id: int
    url: str
    final_url: str | None = None
    status: int | None = None
    error: str | None = None
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    lang: str | None = None
    page_type: str | None = None
    noindex: bool | None = None
    word_count: int | None = None
    main_content: str | None = None
    main_content_method: str | None = None
    external_link_count: int | None = None
    raw_html_hash: str | None = None
    render_ms: int | None = None
    fetched_at: datetime | None = None
    run_id: int | None = None


class Link(Contract):
    """Egy belső link (`links`)."""

    module: ClassVar[str] = "crawl"
    from_page_id: int
    to_url: str
    to_page_id: int | None = None
    anchor: str | None = None
    position: str
    nofollow: bool | None = None
    ordinal: int | None = None


class StructuredData(Contract):
    """Egy oldal egy strukturált adat-eleme, a jelölés fajtájától függetlenül."""

    module: ClassVar[str] = "crawl"
    renamed: ClassVar[dict[str, str]] = {"json": "data"}
    page_id: int
    syntax: StructuredSyntax = "json-ld"
    type: str | None = None
    data: Any = _json()
    ordinal: int | None = None


class PageMeta(Contract):
    """Az oldal fejléc-adatai: canonical, hreflang és a strukturált adat."""

    module: ClassVar[str] = "crawl"
    page_id: int
    canonical: str | None = None
    hreflang: list[str] = Field(default_factory=list)
    structured_data: list[StructuredData] = Field(default_factory=list)

    @field_validator("hreflang", mode="before")
    @classmethod
    def _no_hreflang(cls, value: Any) -> Any:
        return value or []


# --- extract -------------------------------------------------------------------------------

class Block(Contract):
    """A renderelt DOM egy látható szövegblokkja (`blocks`)."""

    module: ClassVar[str] = "extract"
    block_id: int
    page_id: int
    ordinal: int
    kind: str
    region: Literal["content", "chrome"]
    level: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    text: str
    cells: Any = _json()


class MentionSource(Contract):
    """Egy említés egy forrása (`mention_sources`)."""

    module: ClassVar[str] = "extract"
    source: Literal["schema", "rule", "llm"]
    run_id: int
    llm_call_id: int | None = None
    count: int


class Mention(Contract):
    """Egy entitás egy említése egy oldalon (`page_entities`), a forrásaival."""

    module: ClassVar[str] = "extract"
    mention_id: int
    page_id: int
    entity_id: int
    block_id: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    surface_form: str
    position: str
    description: str | None = None
    context: str | None = None
    flags: list[str] = Field(default_factory=list)
    sources: list[MentionSource] = Field(default_factory=list)

    @field_validator("flags", mode="before")
    @classmethod
    def _no_flags(cls, value: Any) -> Any:
        return value or []


class Candidate(Contract):
    """Egy oldal egy puha típusú (concept, service) tétele a bizonyítékaival és a döntéssel
    (`soft_checks`)."""

    module: ClassVar[str] = "extract"
    run_id: int
    page_id: int
    entity_id: int | None = None
    canonical: str
    type: Literal["service", "concept"]
    structure: str | None = None
    blocks: int | None = None
    mentions: int | None = None
    prominent: bool | None = None
    rank: int | None = None
    knowledge: str | None = None
    sol: bool | None = None
    kept: bool
    type_changed_from: str | None = None
    type_change_reason: str | None = None


# --- resolve -------------------------------------------------------------------------------

class Entity(Contract):
    """Egy site-szintű entitás (`entities`), a tudásbázis-mezők nélkül (azok: `KbLink`)."""

    module: ClassVar[str] = "resolve"
    entity_id: int
    name: str
    lang: str | None = None
    type: str
    subtype: str | None = None
    role: str | None = None
    tier: str | None = None
    namespace: str | None = None
    aliases: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    source: str | None = None
    page_id: int | None = None
    anchor_page_id: int | None = None
    attributes: Any = _json()
    type_suggested: str | None = None
    type_votes: Any = _json()
    type_changed_from: str | None = None
    created_at: datetime | None = None

    @field_validator("aliases", "flags", mode="before")
    @classmethod
    def _no_list(cls, value: Any) -> Any:
        return value or []


class Alias(Contract):
    """Egy entitás egy aliasa, nyelvvel és forrással (`entity_aliases`)."""

    module: ClassVar[str] = "resolve"
    entity_id: int
    alias: str
    lang: str | None = None
    source: str


class Relation(Contract):
    """Két entitás kapcsolata (`entity_relations`)."""

    module: ClassVar[str] = "resolve"
    from_id: int
    to_id: int
    type: str
    source: str
    evidence: Any = _json()


class MergeRecord(Contract):
    """Egy összevonás a naplóból (`merge_log`)."""

    module: ClassVar[str] = "resolve"
    merge_id: int
    run_id: int
    kept_id: int
    removed_id: int | None = None
    kept_name: str
    removed_name: str
    rule: str
    evidence: Any = _json()
    merged_at: datetime


class KbLink(Contract):
    """Egy entitás tudásbázis-kapcsolata: Wikidata, Wikipedia és a Google Knowledge Graph
    (`entities` tudásbázis-oszlopai)."""

    module: ClassVar[str] = "resolve"
    entity_id: int
    wikidata_id: str | None = None
    wikidata_status: str | None = None
    wikipedia: str | None = None
    knowledge_checked_at: datetime | None = None
    kg_status: str | None = None
    kg_id: str | None = None
    kg_type: str | None = None
    kg_type_mismatch: bool | None = None
    kg_reason: str | None = None
    wikipedia_url: str | None = None
    validated_at: datetime | None = None


# --- graph ---------------------------------------------------------------------------------

class PageNode(Contract):
    """Egy oldal a gráfban (`page_nodes`)."""

    module: ClassVar[str] = "graph"
    page_id: int
    url: str
    role: str
    support_kind: str | None = None
    title: str | None = None
    h1: str | None = None
    lang: str | None = None
    group_key: str
    hreflang_pages: list[str] = Field(default_factory=list)
    main_status: Literal["main", "support", "none"]
    canonical_page: int | None = None
    canonical_issue: str | None = None
    decision: Any = _json()

    @field_validator("hreflang_pages", mode="before")
    @classmethod
    def _no_pages(cls, value: Any) -> Any:
        return value or []


class Edge(Contract):
    """Egy él a gráfban (`edges`)."""

    module: ClassVar[str] = "graph"
    edge_id: int
    from_kind: NodeKind
    from_id: int
    to_kind: NodeKind
    to_id: int
    type: EdgeType
    source: str
    evidence: Any = _json()
    weight: float | None = None


class MainEntity(Contract):
    """Egy oldal fő vagy másodlagos entitása (`page_main_entity`)."""

    module: ClassVar[str] = "graph"
    page_id: int
    entity_id: int
    role: Literal["main", "secondary"]
    rank: int
    confidence: Confidence
    evidence: Any = _json()
    external_confirmation: Any = _json()


class EntityWeight(Contract):
    """Egy entitás súlya a site-on (`entity_weights`)."""

    module: ClassVar[str] = "graph"
    entity_id: int
    pages: int
    mentions: int
    structural: int
    main_pages: int
    secondary_pages: int
    content_anchors: int
    nav_anchors: int | None = None
    single_mention: bool
    weight: float


# --- findings ------------------------------------------------------------------------------

class Finding(Contract):
    """Egy SEO-megállapítás (`findings`). A `recommendation` (javaslat) ma nem keletkezik."""

    module: ClassVar[str] = "findings"
    finding_id: int
    type: str
    severity: Severity
    page_id: int | None = None
    entity_id: int | None = None
    summary: str
    evidence: Any = _json()
    recommendation: str | None = None


CONTRACTS: tuple[type[Contract], ...] = (
    LLMCall, Page, Link, PageMeta, StructuredData, Block, Mention, MentionSource, Candidate, Entity, Alias,
    Relation, MergeRecord, KbLink, PageNode, Edge, MainEntity, EntityWeight, Finding)
