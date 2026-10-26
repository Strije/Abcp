"""Знания о машинах и деталях — из app/data/podbor_knowledge.json, а не из кода.

В коде робота — только механизмы; что у какой марки как называется, как пишут марки и фирмы кириллицей, какие переводы
каталога вводят в заблуждение — в данных (аудит 26.10.2026: такие таблицы были разбросаны по engine, dialog, catalog, makes).
Ключи «_» в разделах — пояснения для людей. Файла нет или он битый — падаем сразу: без него робот подбирает хуже молча.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "data" / "podbor_knowledge.json"


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def table(section: str) -> dict:
    """Раздел-словарь без пояснения «_»."""
    return {k: v for k, v in data()[section].items() if k != "_"}


def rules(section: str) -> list[dict]:
    return data()[section]["rules"]


def families() -> dict[str, tuple[str, tuple[str, ...]]]:
    return {fam: (f["show"], tuple(f["catalog"])) for fam, f in table("makes")["families"].items()}


def family_words() -> dict[str, tuple[str, ...]]:
    return {fam: tuple(f["words"]) for fam, f in table("makes")["families"].items() if f["words"]}


def renames() -> list[tuple[re.Pattern, re.Pattern, str]]:
    return [(re.compile(r["name"], re.I), re.compile(r["oem"]), r["to"]) for r in rules("catalog_renames")]


def vin_hints() -> list[tuple[re.Pattern, re.Pattern, str]]:
    return [(re.compile(r["ident"]), re.compile(r["words"], re.I), r["text"]) for r in rules("vin_hints")]


def economy_lines() -> list[tuple[re.Pattern, re.Pattern, dict]]:
    return [(re.compile(r["oem"], re.I), re.compile(r["name"], re.I), {"line": r["line"], "maker": r["maker"]})
            for r in rules("economy_lines")]


def kin(section: str, stem) -> dict[str, list[str]]:
    """Другие названия детали: основа слова клиента → основы названий в каталоге."""
    return {stem(w): [stem(a) for a in e["also"]] for w, e in table(section).items()}
