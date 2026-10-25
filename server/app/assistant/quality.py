"""Уровни качества брендов — независимо от гарантий магазина (26.10.2026, руководитель: «нужно качество»).

OE — поставщик детали на конвейер (по сути оригинал без логотипа марки), good — проверенный aftermarket, eco — остальное.
Таблица — app/data/brand_quality.json, правится вручную: выбор не зависит от настроения модели.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "data" / "brand_quality.json"


def key(brand) -> str:
    """«Mann-Filter», «MANN FILTER» → «MANNFILTER»; «Lemförder» → «LEMFORDER»."""
    b = str(brand or "").upper().replace("Ö", "O").replace("Ä", "A").replace("Ü", "U")
    return re.sub(r"[^A-Z0-9]", "", b)


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tier(brand) -> tuple[str, str]:
    """(уровень, почему): ("OE", "поставщик амортизаторов Toyota… на конвейер") / ("good", …) / ("eco", "")."""
    k, t = key(brand), _table()
    if k in t.get("OE", {}):
        return "OE", t["OE"][k]
    if k in t.get("good", {}):
        return "good", t["good"][k]
    return "eco", ""
