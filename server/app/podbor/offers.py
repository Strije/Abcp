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


def lr_vote(rows: list[dict]) -> str:
    """Лево/право по описаниям поставщиков — тем же правилом, что и перед/зад. «прав/лев» не считается:
    это деталь на обе стороны. Нужно, когда каталог назвал сторону только у одной из пары
    («Левая головка блока» и просто «Головка блока цилиндров» у V6 Toyota)."""
    seen: dict[tuple, str] = {}
    for r in rows:
        k = (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))
        d = str(r.get("description") or "")
        if d and k not in seen:
            seen[k] = d
    left = sum(1 for d in seen.values() if T.LEFT.search(d) and not T.RIGHT.search(d))
    right = sum(1 for d in seen.values() if T.RIGHT.search(d) and not T.LEFT.search(d))
    if left >= 2 and left >= 4 * right:
        return "left"
    if right >= 2 and right >= 4 * left:
        return "right"
    return ""


def days(hours: Any) -> int:
    h = _num(hours)
    return 0 if h <= 0 else math.ceil(h / 24)


def _offer(r: dict, tags: list[str] | None = None, count: int = 1, cheaper: dict | None = None) -> dict:
    o = {"brand": r.get("brand"), "number": r.get("number"), "description": r.get("description") or "",
         "price": _num(r.get("price")), "days": days(r.get("deliveryPeriod")),
         "confirm": int(r.get("confirmCount") or 0), "offers": count, "tags": tags or [], "cheaper": None}
    # Тот же артикул дешевле, но дольше: «1 110 ₽ завтра или 900 ₽ через неделю» — пусть клиент выбирает.
    # В разы дешевле (оригинал Ford за 1 640 ₽ при 12 640 ₽) — скорее ошибка в прайсе, такое не предлагаем.
    if cheaper is not None and cheaper is not r:
        p, d = _num(cheaper.get("price")), days(cheaper.get("deliveryPeriod"))
        if o["price"] * 0.3 <= p < o["price"] * 0.95 and d > o["days"]:
            o["cheaper"] = {"price": p, "days": d}
    return o


def _speed(r: dict) -> tuple[int, float]:
    return days(r.get("deliveryPeriod")), _num(r.get("price"))


def _cost(r: dict) -> tuple[float, int]:
    return _num(r.get("price")), days(r.get("deliveryPeriod"))


# Мелочь, которую поставщики привязывают кроссами к номеру детали: к колодкам — «Ремкомплект колодок»,
# к компрессору — клапаны и муфты. Если деталь сама не такая, это не аналог.
_SMALL = {T.stem(w) for w in ("ремкомплект", "пыльник", "датчик", "болт", "гайка", "шайба", "скоба", "пружина",
                               "направляющая", "втулка", "клипса", "крепление", "кронштейн", "клапан", "прокладка",
                               "уплотнитель", "кольцо", "сальник", "трубка", "шланг", "реле", "муфта", "фиксатор",
                               "заглушка", "колпачок", "монтажный", "смазка", "щуп", "наклейка", "разъем",
                               "фишка", "штекер", "клемма", "сайлентблок", "шарнир")}


def not_the_part(description: str, name: str) -> bool:
    """«Ремкомплект передних тормозных колодок» при детали «Колодки тормозные» — не аналог."""
    st = T.stems(T.expand(description))   # «С/блок задний переднего рычага» — сайлентблок, не рычаг
    h = T.head(st) if st else None
    if not h or not any(T.same(h, s) for s in _SMALL):
        return False
    # «ШРУС с пыльником, монтажными деталями…» (VAG) — сам ШРУС: «Пыльник ШРУСа» ему не аналог
    main = re.split(r"\s(?:с|со)\s|,", name)[0]
    return not any(T.same(h, s) for s in T.stems(main))


OUTLIER = 0.12   # 50 ₽ при середине 700 ₽ — 0.07; дешёвые китайские колодки при середине 1 600 ₽ — около 0.2


def curate(rows: list[dict], oem: str, car_brand: str, warranty: set[str], limit: int = 5, name: str = "") -> dict:
    """Оригинал и до `limit` аналогов — по одному на бренд, в порядке «что быстрее привезти».
    В выборку обязательно попадают самый дешёвый, ★ частая замена и бренд с гарантией магазина;
    остальные места — самым быстрым. У каждого артикула — самое быстрое предложение и, если есть,
    более дешёвое, но долгое."""
    clean = [r for r in rows if _num(r.get("price")) > 0 and str(r.get("isUsed") or "0") in ("0", "", "False", "false")]
    if name:
        clean = [r for r in clean if not not_the_part(str(r.get("description") or ""), name)
                 or _key(r.get("numberFix") or r.get("number")).lstrip("0") == _key(oem).lstrip("0")]
    # «CTR — 50 ₽» за рулевую тягу при остальных 450–960 ₽: ошибка в прайсе или не та позиция.
    # Самым дешёвым такое не показываем — отсекаем то, что в разы дешевле середины по артикулам
    low: dict[tuple, float] = {}
    for r in clean:
        k = (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))
        low[k] = min(low.get(k, math.inf), _num(r.get("price")))
    if len(low) >= 5:
        mid = sorted(low.values())[len(low) // 2]
        clean = [r for r in clean if _num(r.get("price")) >= OUTLIER * mid]
    fast: dict[tuple, dict] = {}
    cheap: dict[tuple, dict] = {}
    count: dict[tuple, int] = {}
    for r in clean:
        k = (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))
        count[k] = count.get(k, 0) + 1
        if k not in fast or _speed(r) < _speed(fast[k]):
            fast[k] = r
        if k not in cheap or _cost(r) < _cost(cheap[k]):
            cheap[k] = r
    # Ведущий ноль: Laximo пишет «4892 562AA», поставщики — «04892562AA» (Chrysler/Mopar)
    okeys, onum = original_keys(car_brand), _key(oem).lstrip("0")
    is_orig = lambda k: k[0] in okeys and k[1].lstrip("0") == onum  # noqa: E731
    ok = next((k for k in fast if is_orig(k)), None)
    original = _offer(fast[ok], ["оригинал"], count[ok], cheap[ok]) if ok else None

    # Один артикул на бренд: Krauf с тремя номерами за одну цену — это один вариант, а не три
    per_brand: dict[str, tuple] = {}
    for k in fast:
        if not is_orig(k) and (k[0] not in per_brand or _speed(fast[k]) < _speed(fast[per_brand[k[0]]])):
            per_brand[k[0]] = k
    pool = list(per_brand.values())
    by_speed = sorted(pool, key=lambda k: _speed(fast[k]))
    by_cost = sorted(pool, key=lambda k: _cost(cheap[k]))

    tags: dict[tuple, list[str]] = {}
    if pool:
        tags.setdefault(by_cost[0], []).append("дешевле всего")
        tags.setdefault(by_speed[0], []).append("быстрее всего")
        frequent = [k for k in by_cost if int(fast[k].get("confirmCount") or cheap[k].get("confirmCount") or 0) >= 2]
        if frequent:
            tags.setdefault(frequent[0], []).append("частая замена")
        guaranteed = [k for k in by_cost if k[0] in warranty]
        if guaranteed:
            tags.setdefault(guaranteed[0], []).append("гарантия магазина")
    chosen = list(tags)[:limit]
    for k in by_speed:
        if len(chosen) >= limit:
            break
        if k not in chosen:
            chosen.append(k)
    chosen.sort(key=lambda k: _speed(fast[k]))
    prices = [_num(r.get("price")) for r in cheap.values()]
    return {
        "original": original,
        "analogs": [_offer(fast[k], tags.get(k, []), count[k], cheap[k]) for k in chosen],
        "stats": {"offers": len(clean), "articles": len(fast),
                  "price_min": min(prices) if prices else 0, "price_max": max(prices) if prices else 0},
        # Все номера-кроссы оригинала: по ним видно, что оригинал определён верно, даже если менеджер
        # ответил аналогом не из показанных пяти (и для поиска по номеру, который назвал клиент)
        "numbers": sorted({k[1] for k in fast}),
    }
