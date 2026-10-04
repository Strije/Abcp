"""Голосование поставщиков за бренд — те же случаи, что в приложении (BrandVotingTest.kt) и в Pricer.

Списки брендов — настоящие ответы search/brands магазина (запись Pricer 02.10.2026): ABCP отдаёт
все бренды «в наличии», и без голосования приложение каждый раз спрашивает.
"""
import asyncio

from app.brand_vote import group_key, pick_brand, vote_brands, vote_for_number

BRANDS_162622 = [{"brand": "Acemark", "number": "162622", "availability": 1, "description": "Комплект прокладок"},
                 {"brand": "ZIC", "number": "162622", "availability": 1, "description": "Масло моторное ZIC X5 10W-40 4 л."}]
BRANDS_W71295 = [{"brand": b, "number": "W71295", "availability": 1, "description": "Фильтр масляный"}
                 for b in ("KIOSHI", "MANN-FILTER (China)", "MANN-FILTER", "Redskin")]
BRANDS_OC90 = [{"brand": "AM POINT", "number": "OC90", "availability": 1, "description": "Фильтр масляный"},
               {"brand": "Mahle/Knecht", "number": "OC90", "availability": 1, "description": "Масляный фильтр"}]


def offer(brand, number, distributor, description="", **extra):
    return {"brand": brand, "number": number, "numberFix": number.replace("-", ""), "description": description,
            "distributorId": distributor, "itemKey": f"{distributor}-{brand}-{number}", "deliveryPeriod": 48, **extra}


def sellers(brand, number, n, start=1, description=""):
    return [offer(brand, number, f"d{i}", description) for i in range(start, start + n)]


def test_clear_majority_is_picked():
    voting = vote_brands("162622", BRANDS_162622, {
        "Acemark": sellers("Acemark", "162622", 1, start=10),
        "ZIC": sellers("ZIC", "162622", 6) + [offer("Acemark", "162622", "d10")],  # аналог из чужого ответа — один раз
    })
    assert [(v["brand"], v["votes"]) for v in voting["votes"]] == [("ZIC", 6), ("Acemark", 1)]
    assert voting["answered"] == 7
    assert pick_brand(voting)["brand"] == "ZIC"


def test_mentions_count_and_same_brand_rows_merge():
    voting = vote_brands("W71295", BRANDS_W71295, {
        "MANN-FILTER": sellers("MANN-FILTER", "W71295", 5) + sellers("MANN-FILTER (China)", "W71-295", 2, start=6),
        "Redskin": sellers("Redskin", "W71295", 2, start=20, description="Фильтр масляный [ан. MANN-FILTER W712/95]")
                   + sellers("Redskin", "W71295", 2, start=22),
        "KIOSHI": sellers("KIOSHI", "W71295", 1, start=30),
    })
    mann = voting["votes"][0]
    assert (mann["brand"], mann["votes"], mann["mentions"]) == ("MANN-FILTER", 7, 2)
    assert pick_brand(voting)["brand"] == "MANN-FILTER"
    assert group_key("MANN-FILTER (China)") == group_key("MANN-FILTER") == group_key("Mann")


def test_close_vote_asks():
    voting = vote_brands("OC90", BRANDS_OC90, {
        "AM POINT": sellers("AM POINT", "OC90", 6, start=10) + [offer("AM POINT", "OC90", "d16", "Фильтр [ан. KNECHT OC90]")],
        "Mahle/Knecht": sellers("Mahle/Knecht", "OC90", 7),
    })
    mahle = next(v for v in voting["votes"] if v["brand"] == "Mahle/Knecht")
    assert (mahle["votes"], mahle["mentions"]) == (7, 1)
    assert pick_brand(voting) is None  # 8 против 7 — не перевес в 1,5 раза


def test_guest_rows_vote_by_server_stars():
    def guest(brand, stars, price):  # гостевой ответ: без поставщиков, но с ★
        return {"brand": brand, "number": "162622", "numberFix": "162622", "price": price, "confirmCount": stars}
    voting = vote_brands("162622", BRANDS_162622, {"ZIC": [guest("ZIC", 6, 900), guest("ZIC", 6, 950)],
                                                   "Acemark": [guest("Acemark", 1, 300)]})
    assert [v["votes"] for v in voting["votes"]] == [6, 1] and voting["answered"] == 7
    assert pick_brand(voting)["brand"] == "ZIC"


def test_own_pickup_warehouses_are_one_supplier():
    own = [offer("ZIC", "162622", f"own{i}", deliveryPeriod=0, deadlineReplace="Хрусталёва 111 (самовывоз)") for i in (1, 2)]
    voting = vote_brands("162622", BRANDS_162622, {"ZIC": own, "Acemark": sellers("Acemark", "162622", 1, start=10)})
    assert next(v for v in voting["votes"] if v["brand"] == "ZIC")["votes"] == 1
    assert pick_brand(voting) is None


def test_minority_leader_and_failed_requests_ask():
    rows = [{"brand": b, "number": "1", "availability": 1} for b in "ABCD"]
    voting = vote_brands("1", rows, {"A": sellers("A", "1", 3), "B": sellers("B", "1", 1, 10),
                                     "C": sellers("C", "1", 1, 20), "D": sellers("D", "1", 2, 30)})
    assert voting["votes"][0]["brand"] == "A" and pick_brand(voting) is None  # 3 из 7
    assert pick_brand(vote_brands("1", rows, {})) is None


def test_vote_for_number_asks_in_parallel_and_survives_errors():
    asked = []

    async def fetch(brand):
        asked.append(brand)
        if brand == "Acemark":
            raise TimeoutError("ABCP не ответил")
        return sellers("ZIC", "162622", 3)

    voting = asyncio.run(vote_for_number("162622", BRANDS_162622, fetch))
    assert sorted(asked) == ["Acemark", "ZIC"]
    assert pick_brand(voting)["brand"] == "ZIC"  # 3 из 3, у второго 0
    single = asyncio.run(vote_for_number("X", [{"brand": "Knecht"}], fetch))
    assert pick_brand(single) == {"brand": "Knecht"} and asked.count("Knecht") == 0  # один бренд — без запросов
