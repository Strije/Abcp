"""Автовыбор бренда голосованием поставщиков — серверная половина того же правила, что в приложении
(app/.../abcp/BrandVoting.kt) и в Pricer (engine.search_brands + app/search.py choose_brand).
Описание и примеры — docs/brand-voting.md.

ABCP отдаёт на номер один общий список брендов (search/brands) без разбивки по поставщикам. Голос
бренда — сколько разных поставщиков продают именно этот номер этого бренда (по ответам search/articles,
поставщик — как у ★: provider_of, склады АвтоДруг — один). В гостевых ответах поставщиков нет — тогда
голос это ★ (confirmCount). Плюс упоминания: аналоги пишут настоящего производителя в названии
(«Фильтр масляный [ан. MAHLE OC90]»).

Бренд берём без вопроса, если у него не меньше половины ответивших поставщиков и он в 1,5 раза впереди
второго (с упоминаниями); один бренд (в том числе MANN-FILTER + MANN-FILTER (China)) — берём сразу.

Использование (подбор, гостевой поиск — что угодно, что умеет получить предложения по номеру и бренду):

    voting = await vote_for_number(number, brand_rows, fetch=lambda brand: guest_offers(a, number, brand, profile, False))
    row = pick_brand(voting)            # строка search/brands или None — показать выбор
"""
import asyncio
import re
from typing import Any, Awaitable, Callable

from .abcp import BRAND_ALIASES, _key, provider_of

SHARE = 0.5
LEAD = 1.5
MAX_BRANDS = 6  # на каждый бренд — один запрос search/articles

_PARENS = re.compile(r"\([^)]*\)")


def group_key(brand: Any) -> str:
    """Ключ бренда без уточнений в скобках: «MANN-FILTER (China)», «MANN-FILTER» и «MANN» — один бренд."""
    k = _key(_PARENS.sub(" ", str(brand or "")))
    return BRAND_ALIASES.get(k, k)


def _available(row: dict) -> bool:
    return str(row.get("availability") or "0").strip().lower() not in ("", "0", "false", "-1")


def _suppliers(rows: list[dict]) -> int:
    ids = {provider_of(r) for r in rows} - {""}
    if ids:
        return len(ids)
    return max((int(r.get("confirmCount") or 0) for r in rows), default=0)


def _mention_patterns(brands: list[str]) -> list[re.Pattern]:
    words = []
    for brand in brands:
        full = _PARENS.sub(" ", brand).strip()
        words += [full] + full.split("/")
    words = list(dict.fromkeys(re.sub(r"\s+", " ", w.strip().upper()) for w in words))
    return [re.compile(r"(?<![A-ZА-ЯЁ0-9])" + re.escape(w) + r"(?![A-ZА-ЯЁ0-9])")
            for w in words if sum(ch.isalnum() for ch in w) >= 3]


def vote_brands(number: str, brand_rows: list[dict], offers_by_brand: dict[str, list[dict]]) -> dict:
    """Голоса по уже полученным предложениям. brand_rows — ответ search/brands, offers_by_brand — ответ
    search/articles на каждый опрошенный бренд. Возвращает {"votes": [...лучшие сверху], "answered": N},
    в каждом голосе: row (строка search/brands, которую открыть), brand, votes, mentions, score."""
    wanted = _key(number)
    seen, exact = set(), []
    for row in (r for rows in offers_by_brand.values() for r in rows or []):
        if wanted not in (_key(row.get("number")), _key(row.get("numberFix"))):
            continue
        # одно и то же предложение приходит и в ответе по другому бренду (как аналог) — считаем один раз
        ident = (provider_of(row), group_key(row.get("brand")),
                 row.get("itemKey") or f"{row.get('description')}|{row.get('price')}")
        if ident not in seen:
            seen.add(ident)
            exact.append(row)
    groups: dict[str, list[dict]] = {}
    for row in brand_rows:
        k = group_key(row.get("brand"))
        if k:
            groups.setdefault(k, []).append(row)
    by_group: dict[str, list[dict]] = {}
    for row in exact:
        by_group.setdefault(group_key(row.get("brand")), []).append(row)
    out = []
    for index, (k, members) in enumerate(groups.items()):
        offers = by_group.get(k, [])

        def own(member):  # под какой строкой ABCP больше поставщиков — её и открываем
            rows = [r for r in offers if str(r.get("brand") or "").upper() == str(member.get("brand") or "").upper()]
            return _suppliers(rows), _available(member)

        best = max(members, key=own)
        patterns = _mention_patterns([str(m.get("brand") or "") for m in members])
        mentions = sum(1 for r in exact if group_key(r.get("brand")) != k
                       and any(p.search(str(r.get("description") or "").upper()) for p in patterns))
        votes = _suppliers(offers)
        out.append({"row": best, "brand": best.get("brand"), "votes": votes, "mentions": mentions,
                    "score": votes + mentions, "_index": index})
    out.sort(key=lambda v: (-v["score"], -v["votes"], v["_index"]))  # при равенстве — порядок ABCP
    for v in out:
        del v["_index"]
    ids = {provider_of(r) for r in exact} - {""}
    # без данных о поставщиках (гость) — сумма голосов: так большинство набрать труднее, а не легче
    return {"votes": out, "answered": len(ids) if ids else sum(v["votes"] for v in out)}


def pick_brand(voting: dict, share: float = SHARE, lead: float = LEAD) -> dict | None:
    """Строка search/brands, которую открываем без вопроса, или None — пусть выберет человек."""
    votes = voting.get("votes") or []
    if not votes:
        return None
    if len(votes) == 1:
        return votes[0]["row"]
    first, second = votes[0], votes[1]
    total = max(int(voting.get("answered") or 0), 1)
    if first["votes"] >= share * total and first["score"] >= lead * max(second["score"], 0.5):
        return first["row"]
    return None


async def vote_for_number(number: str, brand_rows: list[dict],
                          fetch: Callable[[str], Awaitable[list[dict]]]) -> dict:
    """Опросить предложения по первым MAX_BRANDS брендам параллельно и посчитать голоса.
    Бренд, по которому запрос не прошёл, остаётся без голосов — решит человек."""
    if len(brand_rows) < 2:
        return vote_brands(number, brand_rows, {})
    asked = [str(r.get("brand") or "") for r in brand_rows[:MAX_BRANDS] if r.get("brand")]
    answers = await asyncio.gather(*(fetch(b) for b in asked), return_exceptions=True)
    offers = {b: (a if isinstance(a, list) else []) for b, a in zip(asked, answers)}
    return vote_brands(number, brand_rows, offers)
