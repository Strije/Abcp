"""Предложения ABCP по номеру: бренд оригинала, сторона по описаниям поставщиков, отбор 3–5 вариантов.

По одному номеру ABCP отдаёт сотни строк (колодки VAG — 600+), читать это клиенту нельзя.
Описания поставщиков — ещё и проверка стороны: у 5K0698151A «передн» встречается 388 раз
против 4 «задн», у 5K0698451B — 14 против 480.

Бренды сравниваем через справочник ABCP (app/brands.py): «Fag» и «FAG», «Lemf» и «LEMFORDER» —
один бренд, поэтому один и тот же аналог от разных поставщиков склеивается в одну строку.
"""
import math
import re
from typing import Any

from .. import brands as AB
from ..abcp import _key, _num
from . import brands as B
from . import text as T

_OEM = B.load()
_MAKES = {_key(m): b for m, b in _OEM.get("makes", {}).items()}
_FAMILIES = {_key(b): fam for b, fam in _OEM.get("families", {}).items()}
_EXTRA = {_key(b): fam for b, fam in _OEM.get("extra", B.EXTRA_FAMILY).items()}


def bkey(brand: Any) -> str:
    return AB.get().key(brand)


def oem_brand_for(car_brand: str) -> str:
    """Под каким брендом ABCP ведёт оригинал этой марки: полный справочник брендов ABCP,
    выжимка data/oem_brands.json, а если марки нет нигде — как oemBrandFor в приложении."""
    full = AB.get()
    if full.loaded and AB.norm(car_brand) in full.alias:
        return full.canon(car_brand)
    hit = _MAKES.get(_key(car_brand))
    if hit:
        return hit
    b = re.sub(r"[^A-Z]", "", car_brand.upper())
    if b in ("VOLKSWAGEN", "VW", "AUDI", "SKODA", "SEAT", "CUPRA"):
        return "VAG"
    return {"LEXUS": "TOYOTA", "INFINITI": "NISSAN", "MINI": "BMW", "DACIA": "RENAULT",
            "CHEVROLET": "GENERAL MOTORS", "OPEL": "GENERAL MOTORS", "CADILLAC": "GENERAL MOTORS",
            "DAEWOO": "GENERAL MOTORS", "MERCEDESBENZ": "MERCEDES", "MERCEDES": "MERCEDES"}.get(b, car_brand.strip())


def original_keys(car_brand: str) -> set[str]:
    """Бренды, под которыми продаётся оригинал: основной, сама марка и его группа в ABCP
    (FORD + MOTORCRAFT + FOMOCO, Hyundai-KIA + Genesis)."""
    main = oem_brand_for(car_brand)
    keys = {bkey(car_brand), bkey(main)} | AB.get().family(main)
    keys |= {bkey(x) for x in _FAMILIES.get(_key(main), []) + _EXTRA.get(_key(main), [])}
    if keys & {"HYUNDAI", "KIA", "HYUNDAIKIA"}:
        keys |= {bkey("Hyundai-KIA"), "HYUNDAIKIA", "MOBIS"}
    return {k for k in keys if k}


def brand_candidates(brands: list[dict], car_brand: str) -> list[str]:
    """Бренды оригинала из «Вы искали» (search/brands) по порядку: основной (FORD, VAG), сама марка,
    остальные из группы (MOTORCRAFT, FOMOCO), потом «Hyundai-KIA» ⊃ HYUNDAI."""
    main, own = bkey(oem_brand_for(car_brand)), bkey(car_brand)
    keys = original_keys(car_brand)
    names = list(dict.fromkeys(str(b.get("brand") or "") for b in brands if b.get("brand")))

    def rank(n: str) -> int | None:
        k = bkey(n)
        if k == main:
            return 0
        if k == own:
            return 1
        if k in keys:
            return 2
        if any(len(x) >= 3 and (x in _key(n) or _key(n) in x) for x in keys):
            return 3
        return None

    ranked = sorted((r, i, n) for i, n in enumerate(names) if (r := rank(n)) is not None)
    return [n for _, _, n in ranked]


def axis_vote(rows: list[dict]) -> tuple[str, int, int]:
    """Сторона по описаниям поставщиков: каждый артикул считаем один раз.
    Решаем, только если одна сторона явно перевешивает (≥3 упоминаний и в 4 раза больше)."""
    seen: dict[tuple, str] = {}
    for r in rows:
        k = (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))
        d = str(r.get("description") or "")
        if d and k not in seen:
            seen[k] = d
    front = sum(1 for d in seen.values() if T.FRONT.search(d) and not T.REAR.search(d))
    rear = sum(1 for d in seen.values() if T.REAR.search(d) and not T.FRONT.search(d))
    if front >= 3 and front >= 4 * rear:
        return "front", front, rear
    if rear >= 3 and rear >= 4 * front:
        return "rear", front, rear
    return "", front, rear


def days(hours: Any) -> int:
    h = _num(hours)
    return 0 if h <= 0 else math.ceil(h / 24)


def _offer(r: dict, tags: list[str] | None = None, count: int = 1) -> dict:
    return {"brand": r.get("brand"), "number": r.get("number"), "description": r.get("description") or "",
            "price": _num(r.get("price")), "days": days(r.get("deliveryPeriod")),
            "confirm": int(r.get("confirmCount") or 0), "offers": count, "tags": tags or []}


def curate(rows: list[dict], oem: str, car_brand: str, warranty: set[str], limit: int = 5) -> dict:
    """Оригинал и до `limit` аналогов: самый дешёвый, самый быстрый, ★ частая замена,
    бренд с гарантией магазина, дальше — по цене."""
    clean = [r for r in rows if _num(r.get("price")) > 0 and str(r.get("isUsed") or "0") in ("0", "", "False", "false")]
    best: dict[tuple, dict] = {}
    count: dict[tuple, int] = {}
    for r in clean:
        k = (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))
        count[k] = count.get(k, 0) + 1
        cur = best.get(k)
        if cur is None or (_num(r.get("price")), days(r.get("deliveryPeriod"))) < \
                (_num(cur.get("price")), days(cur.get("deliveryPeriod"))):
            best[k] = r
    okeys, onum = original_keys(car_brand), _key(oem)
    original = next((_offer(r, ["оригинал"], count[k]) for k, r in best.items() if k[0] in okeys and k[1] == onum), None)
    analogs = {k: r for k, r in best.items() if not (k[0] in okeys and k[1] == onum)}

    picks: dict[tuple, list[str]] = {}

    def add(key, tag):
        if key is not None:
            picks.setdefault(key, []).append(tag)

    by_price = sorted(analogs, key=lambda k: (_num(analogs[k].get("price")), days(analogs[k].get("deliveryPeriod"))))
    if by_price:
        add(by_price[0], "дешевле всего")
        add(min(analogs, key=lambda k: (days(analogs[k].get("deliveryPeriod")), _num(analogs[k].get("price")))),
            "быстрее всего")
        frequent = [k for k in by_price if int(analogs[k].get("confirmCount") or 0) >= 2]
        add(frequent[0] if frequent else None, "частая замена")
        guaranteed = [k for k in by_price if k[0] in warranty]
        add(guaranteed[0] if guaranteed else None, "гарантия магазина")
    for k in by_price:
        if len(picks) >= limit:
            break
        picks.setdefault(k, [])
    chosen = sorted(list(picks.items())[:limit], key=lambda kv: _num(analogs[kv[0]].get("price")))
    prices = [_num(r.get("price")) for r in best.values()]
    return {
        "original": original,
        "analogs": [_offer(analogs[k], tags, count[k]) for k, tags in chosen],
        "stats": {"offers": len(clean), "articles": len(best),
                  "price_min": min(prices) if prices else 0, "price_max": max(prices) if prices else 0},
    }
