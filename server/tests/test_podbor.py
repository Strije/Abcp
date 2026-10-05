"""Подбор по заявке без настоящих Laximo и ABCP. Данные повторяют живые ответы (04.10.2026):
Ford Focus III — задняя «ступица колеса» в группе «Подшипник ступичный», воздушный фильтр
«Фильтрующий элемент»; VW Polo — два одинаковых комплекта колодок, сторона только в примечании узла."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from app import brands as AB
from app.podbor import Engine
from app.podbor import text as T
from app.podbor.offers import axis_vote, brand_candidates, curate

FORD = {"catalog": "FORD202201", "brand": "FORD", "name": "Focus CB8 2011-2015", "vehicleId": "0", "ssd": "$ford$",
        "attributes": [{"key": "engine", "value": "1.6L Duratec Ti-VCT (123PS) - Sigma"},
                       {"key": "date", "value": "10/01/2012"}, {"key": "manufactured", "value": "2012"},
                       {"key": "options", "value": "очень длинный список опций"}]}
POLO = {"catalog": "VW1587", "brand": "VOLKSWAGEN", "name": "Polo", "vehicleId": "1", "ssd": "$vw$",
        "attributes": [{"key": "engine_info", "value": "1600CC / 110hp"}, {"key": "date", "value": "22.01.2021"}]}
TOY = {"catalog": "TOYOTA00", "brand": "TOYOTA", "name": "CAMRY", "vehicleId": "2", "ssd": "$toy$", "attributes": []}
VEHICLES = {"X9FKXXEEBKCB57566": [FORD], "XW8ZZZCKZMG026007": [POLO], "XW7BF4FK30S064389": [TOY],
            "WVWZZZ1KZ6W000001": [POLO, FORD]}


def node(gid, name, syn="", link=True, children=()):
    return {"name": name, "quickGroupId": gid, "synonyms": syn, "link": link, "children": list(children)}


TREE = node(0, "Легковые автомобили (NEW)", link=False, children=[
    node(1, "Детали для ТО", link=False, children=[
        node(2, "Фильтр масляный"), node(3, "Фильтр воздушный", "воздухан"), node(5, "Фильтр салонный", "Салонник"),
        node(15, "Колодки тормозные")]),
    node(450, "Ходовая часть", link=False, children=[
        node(488, "Ступица колеса, составляющие", children=[node(489, "Ступица колеса"),
                                                            node(490, "Подшипник ступичный", "Подшипник ступицы")]),
        node(13402, "Рычаг передний нижний", "нижний рычаг")]),
    node(540, "Тормозная система", link=False, children=[node(561, "Стояночный тормоз", "Колодки ручника")]),
])


def det(oem, name, amount="1", match=True, note=""):
    attrs = [{"key": "amount", "value": amount, "name": "Количество"}]
    if note:
        attrs.append({"key": "note", "value": note, "name": "Примечание"})
    return {"name": name, "oem": oem, "codeOnImage": "1", "match": match, "attributes": attrs}


def unit(name, details, note=""):
    return {"unitId": "7", "name": name, "ssd": "$u$", "imageUrl": "https://img.laximo.ru/x/%size%/a.gif",
            "attributes": [{"key": "note", "value": note, "name": "Примечание"}] if note else [], "details": details}


def cat(name, *units):
    return {"categoryId": "1", "name": name, "units": list(units)}


DETAILS = {
    ("FORD202201", 490): [
        cat("Передн.Хобразн.эл-т,кулак&ступица", unit("Повор.кулак и ступица пер.колеса", [
            det("2215574", "Подшипник ступицы колеса", "2"),
            det("2472586", "Ремкомплект подшипника колеса, Сервисный комплект", "2")])),
        cat("Задн.поперечн. эл-т,кулак&ступица", unit("Задний кулак и рычаги подвески", [
            det("2101656", "Ступица колеса", "2", note="Без автоматич. сист.парковки")]))],
    ("FORD202201", 2): [cat("Двигатель", unit("Масляный радиатор и фильтр", [det("1883037", "Масляный фильтр")]))],
    ("FORD202201", 3): [cat("Двигатель", unit("Воздушный фильтр, Zetec 1.6", [
        det("1848220", "Фильтрующий элемент, Дополнительно закажите маслоотделительный пеноматериал")]))],
    ("FORD202201", 5): [cat("Отопление", unit("Отопит./вентилятор кондиц.и эл-ты", [
        det("1709013", "Фильтр в сборе, Фильтр устран. запаха и частиц")]))],
    ("FORD202201", 15): [
        cat("Тормоза", unit("Диски и суппорты передних тормозов", [
            det("1712024", "Комплект торм. колодок суппорта"),
            det("1809256", "Комплект торм. колодок суппорта, Не для гарантийного ремонта автомобиля, Motorcraft")])),
        cat("Тормоза", unit("Диски и суппорты задних тормозов", [det("1683374", "Комплект тормозных колодок")]))],
    ("FORD202201", 561): [cat("Тормоза", unit("Стояночный тормоз", [det("1900071", "Трос стояночного тормоза")]))],
    ("FORD202201", 13402): [cat("Подвеска", unit("Передняя подвеска", [
        det("545004L000", "Рычаг подвески передний нижний левый"),
        det("545004L100", "Рычаг подвески передний нижний правый")]))],
    ("VW1587", 15): [cat("Колёса, тормозная система",
                         unit("Дисковые тормоза", [det("5K0698151A", "1 комплект тормозных колодок для дисковых тормозов")],
                              note="Дисковые тормоза;передн.;PR:1ZC"),
                         unit("Дисковые тормоза", [det("5K0698451B", "1 комплект тормозных колодок для дисковых тормозов")],
                              note="Дисковые тормоза;PR:1KT"))],
    # Каталог стороны не пишет — решают описания поставщиков
    ("TOYOTA00", 15): [cat("Тормоза", unit("Колодки", [det("04465-33471", "PAD KIT, DISC BRAKE"),
                                                       det("04466-33180", "PAD KIT, DISC BRAKE")]))],
}
# Что в составе группы, а что только в узле (all=true): воздушный фильтр Ford — только по составу
FULL_EXTRA = {("FORD202201", 3): [det("1745844", "Впуск. труб. воздушного фильтра")]}


def offer(brand, number, price, hours=96, desc="", confirm=1):
    return {"brand": brand, "number": number, "numberFix": number, "price": str(price), "deliveryPeriod": str(hours),
            "description": desc, "confirmCount": confirm}


OFFERS = {
    ("2101656", "FORD"): [offer("FORD", "2101656", 9020, 236, "Focus 2011-> задний"),
                          offer("Zekkert", "RL1388", 4500, 21, "Ступица задняя (с ABS) Ford Focus III"),
                          offer("GSP", "GK3907", 1530, 93, "Ступица", 2),
                          offer("Fag", "713679190", 9080, 134, "СТУПИЦА ЗАДНЯЯ FORD FOCUS III"),
                          offer("FAG", "713679190", 9500, 20, "СТУПИЦА ЗАДНЯЯ FORD FOCUS III")],
    ("04465-33471", "TOYOTA"): [offer("TOYOTA", "04465-33471", 9000)] +
                               [offer(b, f"P{i}", 900 + i, desc="Колодки тормозные передние") for i, b in enumerate("ABCD")],
    ("04466-33180", "TOYOTA"): [offer("TOYOTA", "04466-33180", 7000)] +
                               [offer(b, f"R{i}", 700 + i, desc="Колодки торм. задние") for i, b in enumerate("ABCD")],
}


class Fake:
    def __init__(self):
        self.calls: list[tuple] = []

    async def laximo(self, method, params):
        return self.answer(method, params)

    def answer(self, method, params):
        self.calls.append((method, params.get("quickGroupId"), params.get("all")))
        if method == "findVehicle":
            return VEHICLES.get(params["identString"], [])
        if method == "listQuickGroup":
            return TREE
        if method == "listQuickDetail":
            key = (params["catalog"], int(params["quickGroupId"]))
            data = [dict(c, units=[dict(u, details=list(u["details"])) for u in c["units"]]) for c in DETAILS.get(key, [])]
            if params["all"] == "true" and key in FULL_EXTRA and data:
                data[0]["units"][0]["details"] += FULL_EXTRA[key]
            return data
        raise AssertionError(method)

    async def brands(self, number):
        if number.startswith("21") or number.startswith("18") or number.startswith("17"):
            return [{"brand": "FOMOCO"}, {"brand": "FORD"}]  # FOMOCO первым — выбрать всё равно FORD
        return [{"brand": "TOYOTA"}] if number.startswith("04") else [{"brand": "VAG"}]

    async def offers(self, number, brand):
        self.calls.append(("offers", number, brand))
        return OFFERS.get((number, brand), [])


@pytest.fixture(autouse=True)
def brand_table(monkeypatch):
    """Маленький справочник брендов вместо выгрузки ABCP (её в репозитории нет)."""
    table = AB.Brands({"brands": {"FORD": ["Ford"], "VAG": ["Volkswagen", "VW", "Skoda"], "Fag": ["FAG"],
                                  "TOYOTA": ["Lexus"], "MOTORCRAFT": []},
                       "groups": {"FORD": ["FORD", "MOTORCRAFT"]}})
    monkeypatch.setattr(AB, "get", lambda: table)
    return table


def run(text, engine=None, **kw):
    engine = engine or Engine(Fake(), None, {"Zekkert"})
    return asyncio.run(engine.run(text, **kw))


# ---------- текст ----------

def test_parse_abcp_template():
    r = T.parse("Модель авто: FORD FOCUS III Хетчбек 1.6 Ti 125 л.с. 2012 год\n VIN/FRAME: X9FKXXEEBKCB57566\n\n"
                "Запрос состоит из следующих позиций:  Подшипник задней ступицы форд фокус 3")
    assert r.ident == "X9FKXXEEBKCB57566" and r.model.startswith("FORD FOCUS III")
    assert r.chunks == ["Подшипник задней ступицы форд фокус 3"]


def test_parse_free_text_vin_typos_and_plate():
    # Кириллические Х/Т/А/В и буква О вместо нуля — обычное дело в переписке
    r = T.parse("Вот вин ХТА21704ОВ0012345, нужны передние колодки")
    assert r.ident == "XTA217040B0012345"
    assert T.parse("госномер а123вс92, свечи").plate == "А123ВС92"
    assert T.split_chunks("1. диски тормозные передние 2. стойки 1.6 стабилизатора") == \
        ["диски тормозные передние", "стойки 1.6 стабилизатора"]


@pytest.mark.parametrize("text,axis,lr", [
    ("Повор.кулак и ступица пер.колеса", "front", ""),
    ("Задн.поперечн. эл-т,кулак&ступица", "rear", ""),
    ("Дисковые тормоза;передн.;PR:1ZC", "front", ""),
    ("Дисковые тормоза;PR:1KT", "rear", ""),          # только PR-код VAG
    ("Механическая коробка переключения передач", "", ""),
    ("задвижка", "", ""),
    ("Кулак переднего колеса, RH", "front", "right"),
    ("Корпус тормозного суппорта;лев.", "", "left"),
    ("правильные колодки", "", ""),
])
def test_side(text, axis, lr):
    assert T.side(text, {"1Z": "front", "1L": "front", "1K": "rear"}) == T.Side(axis, lr)


def test_same_stems_and_abbreviations():
    assert T.same("ступиц", "ступичн") and T.same("колодк", "колодок")
    assert not T.same("крыш", "крышк")
    assert T.stems("Комплект торм. колодок") == ["комплект", "торм.", "колодок"]
    assert T.same("торм.", "тормозн") and not T.same("торм.", "трос")


def test_fleeting_vowel_and_length_limit():
    assert T.same(T.stem("ремень"), T.stem("ремня")) and T.same(T.stem("бачок"), T.stem("бачка"))
    # Начало слова совпадает, но это другое слово: «стекло» ≠ «стеклоочиститель»
    assert not T.same(T.stem("стекло"), T.stem("стеклоочиститель"))
    assert T.stem("стеклоподъемник") == T.stem("стеклоподьемник")


def test_head_word_skips_adjectives():
    assert T.head(T.stems("топливный фильтр")) == T.stem("фильтр")
    assert T.head(T.stems("датчик положения распредвала")) == T.stem("датчик")
    assert T.stems("Рем. комплект суппорта")[0] == "ремкомплект"


def _index(synonyms=()):
    from app.podbor.catalog import TreeIndex
    tree = node(0, "Легковые", link=False, children=[
        node(1, "Двигатель", link=False, children=[
            node(4, "Фильтр топливный"), node(6, "Насос топливный"), node(11, "Ремень приводной"),
            node(12, "Ремень ГРМ"), node(101, "Электроника двигателя, датчики"), node(142, "Датчик давления масла")]),
        node(310, "Система охлаждения", link=False, children=[node(313, "Выключатель, датчик")]),
        node(56, "Система нагнетания воздуха", "Компрессор,Турбина"),
        node(780, "Кондиционер", link=False, children=[node(768, "Компрессор")]),
        node(620, "Освещение", link=False, children=[node(623, "Фары передние"), node(650, "Фонарь задний")]),
        node(13401, "Ремкомплект насоса ГУР"), node(71, "Комплект ремня ГРМ")])
    return TreeIndex(tree, frozenset(), None, list(synonyms))


def _top(ix, text):
    r = ix.rank(T.stems(text), T.side(text))
    return r[0][0].id if r else None


def test_rank_section_glued_words_and_head():
    ix = _index([{"words": ["ремень генератора"], "groups": [193, 11]},
                 {"words": ["датчик коленвала", "датчик положения распредвала"], "groups": [101]}])
    assert _top(ix, "Компрессор кондиционера") == 768        # раздел «Кондиционер» уточняет группу
    assert _top(ix, "датчик температуры системы охлаждения") == 313
    assert _top(ix, "MANN-FILTER ТОПЛИВНЫЙФИЛЬТР") == 4      # склейка и главное слово «фильтр»
    assert _top(ix, "BMW ДАТЧИККОЛЕНВАЛА") == 101
    assert _top(ix, "ремень генератора") == 11                # 193 у машины нет — следующая группа
    assert _top(ix, "натяжитель ремня генератора") != 12      # «ремня» ~ «ремень»


def test_synonym_side_and_whole_phrase():
    ix = _index([{"words": ["фара задняя", "стоп сигнал"], "groups": [650]},
                 {"words": ["ремкомплект грм"], "groups": [71]},
                 {"words": ["подушка кпп"], "groups": [142]}])
    assert _top(ix, "фара левая") == 623
    assert _top(ix, "фара задняя правая") == 650
    assert _top(ix, "ремкомплект ГРМ") == 71
    # Часть синонима без его главного слова не считается: «кпп» — не «подушка кпп»
    assert _top(ix, "кпп") is None
    assert _top(ix, "подушка") == 142


def test_not_catalog_position():
    p = run("X9FKXXEEBKCB57566 очиститель тормозов 1 баллончик")["positions"][0]
    assert p["status"] == "not_found" and "не деталь каталога" in p["note"]
    assert not p["groups"]


# ---------- подбор ----------

def test_rear_hub_is_an_assembly():
    r = run("VIN/FRAME: X9FKXXEEBKCB57566\nЗапрос состоит из следующих позиций: Подшипник задней ступицы форд фокус 3")
    assert r["status"] == "ok" and r["vehicle"]["catalog"] == "FORD202201"
    p = r["positions"][0]
    assert p["status"] == "found"
    v = p["variants"][0]
    assert (v["oem"], v["axis"], v["side_source"]) == ("2101656", "rear", "каталог")
    assert "ступица в сборе" in p["note"]
    o = v["offers"]
    assert o["original"]["brand"] == "FORD" and o["original"]["price"] == 9020
    # «Fag» и «FAG» — один бренд: одна строка, лучшая цена
    assert sum(1 for a in o["analogs"] if a["number"] == "713679190") == 1
    tags = {t for a in o["analogs"] for t in a["tags"]}
    assert {"дешевле всего", "быстрее всего", "частая замена", "гарантия магазина"} <= tags
    assert "на машину нужно 2 шт." in r["text"] and "9 020 ₽" in r["text"]
    assert "Ford Focus CB8, 2012 г., 1.6 л 123 л.с." in r["text"]
    # Аналоги — по сроку: сначала что привезём быстрее
    assert [a["days"] for a in o["analogs"]] == sorted(a["days"] for a in o["analogs"])


def test_hub_without_side_asks_front_or_rear():
    p = run("X9FKXXEEBKCB57566 подшипник ступицы")["positions"][0]
    assert p["status"] == "choose" and "передние или задние" in p["question"].lower()
    assert {v["oem"] for v in p["variants"]} >= {"2215574", "2101656"}


def test_vag_pads_side_only_in_unit_note():
    front = run("Здравствуйте, вин XW8ZZZCKZMG026007, нужны передние колодки")["positions"][0]
    assert front["query"] == "передние колодки"
    assert [v["oem"] for v in front["variants"]] == ["5K0698151A"]
    rear = run("XW8ZZZCKZMG026007 колодки задние")["positions"][0]
    assert [v["oem"] for v in rear["variants"]] == ["5K0698451B"]   # сторона только из PR:1KT
    both = run("XW8ZZZCKZMG026007 колодки тормозные")["positions"][0]
    assert both["status"] == "choose" and len(both["variants"]) == 2


def test_side_from_supplier_descriptions():
    p = run("XW7BF4FK30S064389 колодки задние")["positions"][0]
    assert [v["oem"] for v in p["variants"]] == ["04466-33180"]
    assert p["variants"][0]["side_source"] == "поставщики"
    assert axis_vote([offer("A", "1", 1, desc="Колодки передние"), offer("B", "2", 1, desc="к-т передн.")]) == ("", 2, 0)


def test_group_members_beat_words():
    """Воздушный фильтр Ford называется «Фильтрующий элемент», а слова «воздушного фильтра» есть
    у впускной трубы из того же узла; трос ручника — в группе «Колодки ручника»."""
    r = run("X9FKXXEEBKCB57566 колодки передние, масляный фильтр, воздушный, салонник")
    got = {p["query"]: [v["oem"] for v in p["variants"]] for p in r["positions"]}
    assert got["масляный фильтр"] == ["1883037"]
    assert got["воздушный"] == ["1848220"]
    assert got["салонник"] == ["1709013"]
    pads = next(p for p in r["positions"] if p["query"] == "колодки передние")
    assert pads["status"] == "found" and "1900071" not in [v["oem"] for v in pads["variants"]]
    assert [v["alt"] for v in pads["variants"]] == [False, True]   # Motorcraft — тот же оригинал
    assert "Тот же оригинал в версии Motorcraft: 1809256" in r["text"]


def test_side_only_piece_repeats_detail():
    r = run("X9FKXXEEBKCB57566 колодки передние и задние")
    assert [(p["side"]["axis"], [v["oem"] for v in p["variants"]][0]) for p in r["positions"]] == \
        [("front", "1712024"), ("rear", "1683374")]


def test_comma_separates_sides():
    """«подшипник ступицы, колодки передние»: «передние» — только про колодки, про подшипник спросим."""
    r = run("X9FKXXEEBKCB57566 подшипник ступицы, колодки передние")
    hub = r["positions"][0]
    assert hub["side"]["axis"] == "" and hub["status"] == "choose"
    assert r["positions"][1]["side"]["axis"] == "front"


def test_left_and_right_are_a_pair_unless_asked():
    p = run("X9FKXXEEBKCB57566 рычаг передний нижний")["positions"][0]
    assert p["status"] == "found" and {v["lr"] for v in p["variants"]} == {"left", "right"}
    p = run("X9FKXXEEBKCB57566 рычаг передний нижний левый")["positions"][0]
    assert [v["oem"] for v in p["variants"]] == ["545004L000"]


def test_vehicle_states():
    assert run("колодки передние")["status"] == "no_vin"
    assert run("XTA219170N0429790 колодки")["status"] == "vehicle_not_found"
    r = run("WVWZZZ1KZ6W000001 колодки")
    assert r["status"] == "choose_vehicle" and len(r["vehicles"]) == 2
    assert run("WVWZZZ1KZ6W000001 колодки передние", vehicle=0)["positions"][0]["variants"][0]["oem"] == "5K0698151A"
    assert run("X9FKXXEEBKCB57566")["status"] == "no_positions"


def test_tree_and_vehicle_cached():
    folder = Path(tempfile.mkdtemp(prefix="podbor-test-"))
    fake = Fake()
    e = Engine(fake, folder)
    run("X9FKXXEEBKCB57566 масляный фильтр", e)
    run("X9FKXXEEBKCB57566 масляный фильтр", e)
    assert [c[0] for c in fake.calls].count("findVehicle") == 1
    assert [c[0] for c in fake.calls].count("listQuickGroup") == 1
    assert (folder / "trees" / "FORD202201.json").exists()
    fake2 = Fake()
    run("X9FKXXEEBKCB57566 масляный фильтр", Engine(fake2, folder))   # новый процесс — дерево с диска
    assert "listQuickGroup" not in [c[0] for c in fake2.calls]


def test_full_units_only_as_fallback():
    fake = Fake()
    run("X9FKXXEEBKCB57566 воздушный фильтр", Engine(fake, None))
    assert ("listQuickDetail", "3", "true") not in fake.calls   # состав группы нашёл — расширенный не нужен


def test_brand_candidates_and_curate():
    assert brand_candidates([{"brand": "FOMOCO"}, {"brand": "BOSCH"}, {"brand": "FORD"}], "FORD") == ["FORD", "FOMOCO"]
    o = curate([offer("MOTORCRAFT", "2101656", 100), offer("BOSCH", "X1", 50)], "2101656", "FORD", set())
    assert o["original"]["brand"] == "MOTORCRAFT" and [a["number"] for a in o["analogs"]] == ["X1"]


def test_compare_model_flags_only_real_mismatch():
    r = run("Модель авто: FORD FOCUS III Хетчбек 1.6 Ti 125 л.с. 2012 год\nVIN/FRAME: X9FKXXEEBKCB57566\n"
            "Запрос состоит из следующих позиций: масляный фильтр")
    assert r["warnings"] == []   # 1.6 и 2012 совпали; «хетчбек» не сверяем — у Ford «5-дверный седан» = хэтчбек
    r = run("Модель авто: FORD FOCUS 2.0 2016\nVIN/FRAME: X9FKXXEEBKCB57566\nЗапрос состоит из следующих позиций: масляный фильтр")
    assert len(r["warnings"]) == 2


# ---------- сервер ----------

def test_podbor_endpoint_hides_purchase_price():
    """POST /v1/podbor на сервере: Laximo и ABCP — заглушки, закупочная цена наружу не уходит."""
    from dataclasses import replace

    import httpx
    from fastapi.testclient import TestClient

    from app.abcp import Abcp
    from app.config import Settings
    from app.laximo import Laximo
    from app.main import create_app

    s = Settings(abcp_host="https://abcp.test", admin_login="admin", admin_md5="a" * 32, token_secret=b"s" * 40,
                 guest_profile_id="777", laximo_user="lx", laximo_pass="pw", podbor_password="секрет-1",
                 state_dir=tempfile.mkdtemp(prefix="podbor-api-"))
    auth = ("менеджер", "секрет-1")
    fake = Fake()

    def laximo(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        return httpx.Response(200, json=fake.answer(request.url.path.rsplit("/", 1)[-1], params))

    def abcp(request: httpx.Request) -> httpx.Response:
        p, q = request.url.path.strip("/"), dict(request.url.params)
        if p == "search/brands":
            return httpx.Response(200, json=[{"brand": "FORD", "number": q["number"], "priceIn": "1"}])
        if p == "search/articles":
            assert q["profileId"] == "777"
            return httpx.Response(200, json=[dict(r, priceIn="123456", distributorId="9")
                                             for r in OFFERS.get((q["number"], q["brand"]), [])])
        return httpx.Response(404, json={"errorMessage": "нет"})

    app = create_app(s, Abcp(s, transport=httpx.MockTransport(abcp)),
                     Laximo(s, transport=httpx.MockTransport(laximo)))
    with TestClient(app) as c:
        # Без пароля и с чужим — нет; браузер получает запрос на вход
        r = c.get("/podbor")
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
        assert c.post("/v1/podbor", json={"text": "X9FKXXEEBKCB57566 свечи"}).status_code == 401
        assert c.get("/podbor", auth=("менеджер", "не тот")).status_code == 401
        # Браузер повторяет запомненный старый пароль — это не перебор, блокировки нет
        for _ in range(30):
            assert c.get("/podbor", auth=("менеджер", "старый")).status_code == 401
        # Перебор разных паролей — блокировка, но правильный пароль проходит
        codes = [c.get("/podbor", auth=("x", f"угадай-{i}")).status_code for i in range(12)]
        assert codes[-1] == 429 and codes.count(401) == 8
        assert c.get("/podbor", auth=auth).status_code == 200
        c.auth = auth
        r = c.post("/v1/podbor", json={"text": "X9FKXXEEBKCB57566 подшипник задней ступицы"})
        assert r.status_code == 200, r.text
        assert r.json()["positions"][0]["variants"][0]["oem"] == "2101656"
        assert "priceIn" not in r.text and "123456" not in r.text and "distributorId" not in r.text
        assert c.get("/podbor").status_code == 200 and "Подбор по VIN" in c.get("/podbor").text
        assert c.post("/v1/podbor", json={"text": "x"}).status_code == 422
    off = create_app(replace(s, laximo_user=""), Abcp(s, transport=httpx.MockTransport(abcp)))
    with TestClient(off) as c:
        c.auth = auth
        assert c.post("/v1/podbor", json={"text": "X9FKXXEEBKCB57566 свечи"}).status_code == 503
    # Пароль не задан — страницы нет совсем
    closed = create_app(replace(s, podbor_password=""), Abcp(s, transport=httpx.MockTransport(abcp)))
    with TestClient(closed) as c:
        c.auth = auth
        assert c.get("/podbor").status_code == 404 and c.post("/v1/podbor", json={"text": "x" * 20}).status_code == 404
