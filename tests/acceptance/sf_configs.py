"""Az elfogadási futás SF-konfigurációi a GUI-ból mentettből.

    python -m tests.acceptance.sf_configs

A `.seospiderconfig` Java-szerializált bináris; az SF CLI csak betölti (`--config`), egyes
beállításait kapcsolóval nem lehet felülírni. A script csak fix méretű primitív mezőket ír át,
így a szerializáció szerkezete és belső hivatkozásai nem változnak. Minden mezőnél ellenőrzi az
osztályleírót (típus, név, szülőosztály nélkül) és a jelenlegi értéket.

- `aaa2-acceptance-desktop.seospiderconfig` (kk.coach, Materia): a mentett, asztali render-
  ablakkal, mint az aaa renderelője (`SpiderRenderWindowConfig`: 1920 × 1080, nem mobil, nem
  érintős). Az SF alapértéke a Googlebot Smartphone (411 × 731, mobil, érintős).
- `aaa2-acceptance-ngx.seospiderconfig`: az asztali, és a crawl a kezdő mappán belül marad
  (`SpiderInternalURLConfig.mCrawlOutsideStartFolder`, `SpiderCrawlConfig.mCheckLinksOutsideFolder`
  hamis). Az ngx seedjének kezdő mappája `/ngx-bootstrap/`, így a crawl ugyanarra szűkül, mint az
  aaa `--include /ngx-bootstrap/`-ja; az SF CLI-nek nincs include-kapcsolója.

Az eredményt a `verify_sf_config.py` méri helyi próba-site-on.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ACCEPTANCE_DIR = Path(__file__).parent
SOURCE = ACCEPTANCE_DIR / "aaa2-acceptance.seospiderconfig"
DESKTOP = ACCEPTANCE_DIR / "aaa2-acceptance-desktop.seospiderconfig"
NGX = ACCEPTANCE_DIR / "aaa2-acceptance-ngx.seospiderconfig"
WINDOW = "seo.spider.config.SpiderRenderWindowConfig"
DESKTOP_WINDOW = (
    (WINDOW, "mWidth", 1920),
    (WINDOW, "mHeight", 1080),
    (WINDOW, "mIsMobile", False),
    (WINDOW, "mTouchEnabled", False),
)
START_FOLDER_ONLY = (
    ("seo.spider.config.SpiderInternalURLConfig", "mCrawlOutsideStartFolder", False),
    ("seo.spider.config.SpiderCrawlConfig", "mCheckLinksOutsideFolder", False),
)
TC_OBJECT, TC_CLASSDESC, TC_STRING, TC_REFERENCE = 0x73, 0x72, 0x74, 0x71
TC_ENDBLOCKDATA, TC_NULL = 0x78, 0x70
PRIMITIVES = {"B": ">b", "C": ">H", "D": ">d", "F": ">f", "I": ">i", "J": ">q", "S": ">h",
              "Z": ">?"}


def primitive_fields(data: bytes, class_name: str) -> dict[str, tuple[str, int]]:
    """Egy közvetlenül példányosított objektum (TC_OBJECT + TC_CLASSDESC, szülőosztály nélkül)
    primitív mezői: név → (típuskód, bájtpozíció)."""
    name = class_name.encode()
    marker = bytes([TC_OBJECT, TC_CLASSDESC]) + struct.pack(">H", len(name)) + name
    if data.count(marker) != 1:
        raise ValueError(f"{class_name}: {data.count(marker)} példányosított leíró, nem 1")
    pos = data.index(marker) + len(marker) + 8 + 1  # serialVersionUID, flags
    (count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    fields = []
    for _ in range(count):
        typecode = chr(data[pos])
        (length,) = struct.unpack_from(">H", data, pos + 1)
        fields.append((typecode, data[pos + 3:pos + 3 + length].decode()))
        pos += 3 + length
        if typecode in "L[":
            if data[pos] == TC_STRING:
                (length,) = struct.unpack_from(">H", data, pos + 1)
                pos += 3 + length
            elif data[pos] == TC_REFERENCE:
                pos += 5
            else:
                raise ValueError(f"{class_name}: váratlan típusleíró: {data[pos]:#x}")
    if data[pos] != TC_ENDBLOCKDATA or data[pos + 1] != TC_NULL:
        raise ValueError(f"{class_name}: a leíró nem zárul le szülőosztály nélkül")
    offset = pos + 2
    found = {}
    for typecode, field in fields:
        if typecode not in PRIMITIVES:
            break
        found[field] = (typecode, offset)
        offset += struct.calcsize(PRIMITIVES[typecode])
    return found


def read_value(data: bytes, class_name: str, field: str) -> object:
    typecode, offset = primitive_fields(data, class_name)[field]
    return struct.unpack_from(PRIMITIVES[typecode], data, offset)[0]


def with_values(data: bytes, changes: tuple[tuple[str, str, object], ...]) -> bytes:
    patched = bytearray(data)
    for class_name, field, value in changes:
        typecode, offset = primitive_fields(bytes(patched), class_name)[field]
        struct.pack_into(PRIMITIVES[typecode], patched, offset, value)
    return bytes(patched)


def main() -> int:
    source = SOURCE.read_bytes()
    desktop = with_values(source, DESKTOP_WINDOW)
    ngx = with_values(desktop, START_FOLDER_ONLY)
    for path, data, changes in ((DESKTOP, desktop, DESKTOP_WINDOW),
                                (NGX, ngx, DESKTOP_WINDOW + START_FOLDER_ONLY)):
        path.write_bytes(data)
        changed = sum(1 for a, b in zip(source, data, strict=True) if a != b)
        print(f"{path.name}: {changed} bájt eltér a mentettől; "
              + ", ".join(f"{field}={read_value(data, cls, field)}" for cls, field, _ in changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
