"""Фото деталей из прайса СТМ поставщика: та же деталь на эту машину, иначе — пример типовой детали, иначе — без фото."""
import asyncio
import json

import httpx

from app.assistant.telegram import KirillBot
from app.podbor.stm import URL, Price, model_tokens

PARTS = [
    {"g3": "Амортизаторы и стойки", "name": "Амортизатор передний (газомасляный) L для LEXUS RX300/RX330/Toyota Harrier 97-03 GANZ GIK1",
     "art": "GIK1", "fits": "", "abc": "B", "photo": "a/l.jpeg"},
    {"g3": "Амортизаторы и стойки", "name": "Амортизатор передний (газомасляный) R для LEXUS RX300/RX330/Toyota Harrier 97-03 GANZ GIK2",
     "art": "GIK2", "fits": "", "abc": "B", "photo": "a/r.jpeg"},
    {"g3": "Отбойники и пыльники амортизатора", "name": "Пыльник+отбойник переднего амортизатора (компл) для LEXUS RX II 300/330 (03-09) GANZ GIK7",
     "art": "GIK7", "fits": "", "abc": "C", "photo": "a/boot.jpeg"},
    {"g3": "Стойки стабилизатора", "name": "Стойки стабилизатора (компл. 2 шт) для ВАЗ 2190 BOLK BSU1", "art": "BSU1",
     "fits": "", "abc": "A", "photo": "a/vaz.jpeg"},
    {"g3": "Стойки стабилизатора", "name": "Стойка стабилизатора передняя L=R для TOYOTA Corolla GANZ GIL1", "art": "GIL1",
     "fits": "", "abc": "C", "photo": "a/toy.jpeg"},
    {"g3": "Втулки", "name": "Втулка рычага переднего для CHEVROLET Matiz GANZ GIL2", "art": "GIL2", "fits": "", "abc": "A",
     "photo": "a/bush.jpeg"},
    {"g3": "Кузовные детали", "name": "Кронштейн переднего бампера L для TOYOTA Camry (XV50) GANZ GIB1", "art": "GIB1",
     "fits": "", "abc": "A", "photo": "a/br.jpeg"},
]


def _price(tmp_path):
    f = tmp_path / "parts.json"
    f.write_text(json.dumps({"parts": PARTS}, ensure_ascii=False), encoding="utf-8")
    return Price(f)


def test_photo_same_car_side_and_examples(tmp_path):
    p = _price(tmp_path)
    assert model_tokens("RX330/350") >= {"RX330", "RX350"}
    r = p.photo_for("амортизатор передний", "Амортизатор передний правый", "LEXUS", "RX330/350")
    assert r == {"url": URL + "a/r.jpeg", "exact": True, "label": PARTS[1]["name"]}          # своя машина и сторона
    # «RX II 300/330» — модель словами прайса раздельно (26.10: пыльник руководителя не нашёлся в каталоге)
    assert p.photo_for("пыльник амортизатора переднего", "", "LEXUS", "RX330/350")["exact"]
    # На эту машину нет, деталь типовая — пример; той же марки (Toyota для Lexus), а не ВАЗ
    r = p.photo_for("стойка стабилизатора переднего", "Шарнир переднего стабилизатора", "LEXUS", "RX330/350")
    assert r["url"].endswith("toy.jpeg") and not r["exact"]
    assert p.photo_for("стойка стабилизатора", "", "KIA", "CEED")["url"].endswith("vaz.jpeg")   # своей марки нет — ходовая
    # Не та деталь: «втулка рычага» — не рычаг, «кронштейн бампера» — не бампер
    assert p.photo_for("рычаг передний нижний", "", "CHEVROLET", "MATIZ") is None
    assert p.photo_for("бампер передний", "Бампер передний", "TOYOTA", "CAMRY") is None
    assert p.photo_for("амортизатор задний", "", "LEXUS", "RX330/350") is None                 # не та ось
    assert Price(tmp_path / "нет.json").photo_for("амортизатор", "", "LEXUS", "RX330") is None  # нет выжимки — без фото


def test_photo_live_catalog_traps(tmp_path):
    """Живой каталог 26.10: Laximo называет машину «LEXUS RX330/350», «CAMRY/HYBRID» — марка и HYBRID не модель;
    «Амортизатор капота» — не амортизатор подвески; шаровая «(верхний рычаг)» — не нижняя."""
    from app.podbor.stm import _owner
    assert model_tokens("LEXUS RX330/350", "LEXUS") == {"RX330", "RX350"} and model_tokens("CAMRY/HYBRID", "TOYOTA") == {"CAMRY"}
    assert _owner("Амортизатор капота TOYOTA Camry") == "капот" and _owner("Сайлентблок задней цапфы TOYOTA") == "цапф"
    assert _owner("Диск тормозной передний перфорированный PRO-LINE с покрытием") == ""
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"parts": [
        {"g3": "Амортизаторы капота", "name": "Амортизатор капота TOYOTA Camry 2006-> GANZ GIA1", "art": "GIA1", "fits": "",
         "abc": "A", "photo": "hood.jpeg"},
        {"g3": "Амортизаторы и стойки", "name": "Амортизатор передний (газомасляный) L для LEXUS NX200 GANZ GIK9", "art": "GIK9",
         "fits": "", "abc": "A", "photo": "nx.jpeg"},
        {"g3": "Опоры шаровые", "name": "Опора шаровая L=R (верхний рычаг) TOYOTA LC Prado/LEXUS GX470 GANZ GIL9", "art": "GIL9",
         "fits": "", "abc": "A", "photo": "up.jpeg"}]}, ensure_ascii=False), encoding="utf-8")
    p = Price(f)
    r = p.photo_for("амортизатор передний", "", "TOYOTA", "CAMRY/HYBRID")   # не капота; подвески — как пример
    assert r["url"].endswith("nx.jpeg") and not r["exact"]
    r = p.photo_for("амортизатор передний", "", "LEXUS", "LEXUS RX330/350")
    assert r and not r["exact"]                                       # NX — пример, не «эта модель»
    assert p.photo_for("шаровая опора нижнего рычага", "", "LEXUS", "LEXUS GX470") is None


def test_bot_sends_photo_with_scheme(tmp_path):
    sent = []

    def handler(request: httpx.Request):
        sent.append((request.url.path.rsplit("/", 1)[-1], request.content))
        ok = not (sent[-1][0] == "sendPhoto" and b"broken" in request.content)
        return httpx.Response(200 if ok else 400, json={"ok": ok})

    class A:
        async def render_scheme(self, sch):
            return (b"jpg", 0)

    bot = KirillBot(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "T", A(), None)
    on_part = bot._parts(1)
    ev = {"block": "🔧 **Амортизатор** · 2 шт.", "maybe": False, "scheme": {"label": "x"},
          "photo": {"url": URL + "a/r.jpeg", "exact": False, "label": "…"}}
    asyncio.run(on_part(ev))
    assert sent[-1][0] == "sendMediaGroup" and json.dumps("фото похожей детали")[1:-1].encode() in sent[-1][1]
    # Без схемы — одно фото; Telegram не смог открыть ссылку — как раньше, текстом
    sent.clear()
    asyncio.run(on_part(dict(ev, scheme=None, photo={"url": "broken", "exact": True, "label": ""})))
    assert [m for m, _ in sent] == ["sendPhoto", "sendMessage"]


def test_kirill_priced_from_stm_fits(tmp_path):
    """Каталог детали не дал — позиция прайса СТМ на эту модель и её предложения ABCP: только самой фирмы, обе стороны,
    с пометкой «менеджер подтвердит» (26.10: пыльник с отбойником Lexus RX330 — в каталоге нет, в прайсе есть)."""
    from types import SimpleNamespace

    from app.assistant import core as C
    from app.podbor import Engine
    from test_podbor import Fake

    class Src(Fake):
        async def brands(self, number):
            return [{"brand": "Ganz"}, {"brand": "Kraft"}]

        async def offers(self, number, brand):
            row = {"number": number, "description": "Амортизатор передний", "price": 5000, "deliveryPeriod": 24, "confirmCount": 2,
                   "distributorId": 1}
            return [dict(row, brand="Ganz"), dict(row, brand="Kraft", price=1000)]

    f = tmp_path / "stm" / "parts.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"parts": [dict(p, brand="GANZ") for p in PARTS]}, ensure_ascii=False), encoding="utf-8")
    a = C.Assistant(Engine(Src(), None, set()), None, "m", tmp_path / "assistant")
    v = SimpleNamespace(brand="LEXUS", name="LEXUS RX330/350", catalog="", summary=lambda: "")
    out = asyncio.run(a.priced_stm(v, {"need": "амортизатор передний"}))
    assert [c["art"] for c in out] == ["GIK1", "GIK2"] and {c["side"] for c in out} == {"левый", "правый"}
    assert all(c["via"] == "stm" and "менеджер подтвердит" in c["note"] for c in out)
    assert {o["brand"] for c in out for o in c["offers"]} == {"Ganz"}          # чужие аналоги по номеру GANZ — мимо
    assert asyncio.run(a.priced_stm(v, {"need": "рычаг передний"})) == []        # нет в прайсе на модель — пусто
