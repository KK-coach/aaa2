"""Az ngx-bootstrap SF-konfigurációja a közösből: a crawl a kezdő mappán belül marad.

    python -m tests.acceptance.make_ngx_config

Az SF CLI-nek nincs include-kapcsolója, és a `.seospiderconfig` Java-szerializált bináris. Két
logikai mezőt állít hamisra:

- `SpiderInternalURLConfig.mCrawlOutsideStartFolder`: nem crawlol a kezdő mappán kívül;
- `SpiderCrawlConfig.mCheckLinksOutsideFolder`: a mappán kívüli linkeket nem is kéri le.

Az ngx seedjének (`/ngx-bootstrap/components`) kezdő mappája `/ngx-bootstrap/`, így a crawl
ugyanarra szűkül, mint az aaa `--include /ngx-bootstrap/`-ja. Csak ezt a két bájtot írja át:
az osztályleírót mezőre értelmezi, a mező pozícióját a megelőző primitív mezők méretéből
számolja, és ellenőrzi, hogy most igaz. Az eredményt a `verify_sf_config.py --ngx` méri.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ACCEPTANCE_DIR = Path(__file__).parent
SOURCE = ACCEPTANCE_DIR / "aaa2-acceptance.seospiderconfig"
TARGET = ACCEPTANCE_DIR / "aaa2-acceptance-ngx.seospiderconfig"
FIELDS = (
    ("seo.spider.config.SpiderInternalURLConfig", "mCrawlOutsideStartFolder"),
    ("seo.spider.config.SpiderCrawlConfig", "mCheckLinksOutsideFolder"),
)
TC_OBJECT, TC_CLASSDESC, TC_STRING, TC_REFERENCE = 0x73, 0x72, 0x74, 0x71
TC_ENDBLOCKDATA, TC_NULL = 0x78, 0x70
PRIMITIVE_SIZES = {"B": 1, "C": 2, "D": 8, "F": 4, "I": 4, "J": 8, "S": 2, "Z": 1}


def primitive_offset(data: bytes, class_name: str, field: str) -> int:
    """Egy közvetlenül példányosított objektum (TC_OBJECT + TC_CLASSDESC, szülőosztály nélkül)
    primitív mezőjének bájtpozíciója."""
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
    for typecode, field_name in fields:
        if field_name == field:
            if typecode != "Z" or data[offset] not in (0, 1):
                raise ValueError(f"{class_name}.{field}: nem logikai mező")
            return offset
        if typecode not in PRIMITIVE_SIZES:
            break
        offset += PRIMITIVE_SIZES[typecode]
    raise ValueError(f"{class_name}.{field}: nincs a primitív mezők között")


def main() -> int:
    data = bytearray(SOURCE.read_bytes())
    for class_name, field in FIELDS:
        offset = primitive_offset(bytes(data), class_name, field)
        if data[offset] != 1:
            raise ValueError(f"{class_name}.{field} értéke {data[offset]}, nem 1")
        data[offset] = 0
        print(f"{class_name.rsplit('.', 1)[-1]}.{field}: 1 → 0 (bájt {offset})")
    TARGET.write_bytes(bytes(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
