"""Az LLM-sémák. Az `EntityType` értékei azonosak az `entities.type` CHECK-jével
(005_entities_llm.sql)."""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

EntityType = Literal[
    "brand", "product", "service", "work", "event", "person", "org", "place", "tech", "concept",
]
ENTITY_TYPES: tuple[str, ...] = get_args(EntityType)

# Típusonként a zárt altípus-lista és a magyarázata (személynél nincs altípus).
SUBTYPE_GLOSSARY: dict[str, dict[str, str]] = {
    "service": {"audit": "felmérés, átvilágítás",
                "implementation": "beállítás, kiépítés, bevezetés",
                "consulting": "tanácsadás", "reporting": "riport, dashboard",
                "training": "képzés, workshop", "other": "egyéb"},
    "org": {"company": "cég", "restaurant": "étterem, vendéglátóhely",
            "institution": "intézmény, szervezet", "team": "csapat", "other": "egyéb"},
    "tech": {"software": "szoftver, alkalmazás, SaaS, platform, felhőszolgáltatás",
             "library": "programkönyvtár", "framework": "keretrendszer",
             "language": "programozási nyelv", "package": "telepíthető csomag (npm, pip)",
             "component": "egy könyvtár konkrét komponense",
             "api_symbol": "dokumentált API-elem (osztály, modul, input, output)",
             "api": "programozói interfész, amit szolgáltatásként hívnak",
             "standard": "műszaki szabvány, specifikáció", "feature": "egy szoftver funkciója",
             "other": "egyéb"},
    "product": {"dish": "étel vagy desszert az étlapon", "ingredient": "alapanyag, tésztafajta",
                "wine": "bor", "drink": "ital, koktél", "physical_good": "egyéb fizikai áru",
                "other": "egyéb"},
    "concept": {"method": "módszer, keretrendszer, eljárás", "discipline": "szakterület",
                "metric": "mérhető mennyiség, mutató", "ui_pattern": "általános felületi minta",
                "license": "licenc", "designation": "eredetvédelmi vagy minőségi jelölés",
                "other": "egyéb"},
    "place": {"city": "város", "region": "régió, tartomány", "country": "ország",
              "venue": "helyszín, épület", "other": "egyéb"},
    "work": {"article": "cikk", "case_study": "esettanulmány", "book": "könyv",
             "course": "tanfolyam", "other": "egyéb"},
    "event": {"conference": "konferencia", "webinar": "webinár", "festival": "fesztivál",
              "other": "egyéb"},
    "brand": {"other": "egyéb"},
    "person": {},
}
SUBTYPE_VOCABULARY: dict[str, tuple[str, ...]] = {
    kind: tuple(values) for kind, values in SUBTYPE_GLOSSARY.items()}
Subtype = Literal[
    "audit", "implementation", "consulting", "reporting", "training", "company", "restaurant",
    "institution", "team", "software", "library", "framework", "language", "package",
    "component", "api_symbol", "api", "standard", "feature", "dish", "ingredient", "wine", "drink",
    "physical_good", "method", "discipline", "metric", "ui_pattern", "license", "designation",
    "city", "region", "country", "venue", "article", "case_study", "book", "course",
    "conference", "webinar", "festival", "other",
]


class ExtractedEntity(BaseModel):
    name: str = Field(description="Canonical name, written as on the page, not translated.")
    type: EntityType
    description: str = Field(description="What this entity is in this text, in one short "
                                         "sentence.")
    evidence: str = Field(description="A quote copied verbatim from the page text that contains "
                                      "the name.")
    context: str = Field(description="The paragraph or list item that contains the evidence, "
                                     "copied from the page.")


class PageExtraction(BaseModel):
    primary_entity: str = Field(description="The entity the page is mainly about, as written on "
                                            "the page; it may be a common noun.")
    entities: list[ExtractedEntity]


class BlockEntity(BaseModel):
    """Egy entitás egy említése egy blokkban (blokkformátumú bemenetnél)."""

    block_id: str = Field(description="The id of the block that contains the mention.")
    surface_form: str = Field(description="The mention exactly as written in that block, "
                                          "inflected form included.")
    canonical_name: str = Field(description="The entity's base name: uninflected, in full form, "
                                            "not translated.")
    type: EntityType
    subtype: Subtype | None = Field(description="One value from the subtype list of the "
                                                "entity's type; null for a person.")
    description: str = Field(description="What the entity is in this text, at most 10 words.")


class NamedEntity(BaseModel):
    """Egy entitás egy felismert említésben (célzott elnevezési hívás)."""

    surface_form: str = Field(description="The words of the mention that name this entity, "
                                          "exactly as in the block text, suffixes included.")
    canonical_name: str = Field(description="The entity's own name: base form, uninflected, in "
                                            "full, not translated.")
    type: EntityType
    subtype: Subtype | None = Field(description="One value from the subtype list of the "
                                                "entity's type; null for a person.")


class NamedMention(BaseModel):
    mention_id: str = Field(description="The id of the mention, as given in the input.")
    entities: list[NamedEntity] = Field(description="The entity or entities the mention names; "
                                                    "at least one.")


class NamingResult(BaseModel):
    mentions: list[NamedMention]


class BlockExtraction(BaseModel):
    primary_entities: list[str] = Field(description="The canonical names of the entities the "
                                                    "page is mainly about; may be empty.")
    entities: list[BlockEntity]


class CandidateDecision(BaseModel):
    candidate_id: str = Field(description="The id of the candidate, as given in the input.")
    keep: bool = Field(description="true: the candidate is what its type asks for; false: it "
                                   "is not.")


class VerifyResult(BaseModel):
    decisions: list[CandidateDecision]
