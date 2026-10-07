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
    # Toyota: сайлентблоки рычага отдельно не продаются — рычаг «в подсборе» (один номер на обе стороны) и фиксатор
    ("TOYOTA00", 13402): [cat("Подвеска", unit("FRONT AXLE ARM & STEERING KNUCKLE", [
        det("48068-48020", "Рычаг передней подвески, нижний правый № 1 (в подсборе)"),
        det("48068-48020", "Рычаг передней подвески, нижний левый № 1 (в подсборе)"),
        det("48657-28010", "Фиксатор сайлентблока переднего нижнего рычага", "2")]))],
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
    ("48068-48020", "TOYOTA"): [offer("TOYOTA", "48068-48020", 15000, desc="Рычаг передний нижний"),
                                offer("Masuma", "RU-380", 580, desc="Сайлентблок переднего рычага"),
                                offer("Zekkert", "GM5000", 370, desc="С/блок задний перед. рычага"),
                                offer("Febest", "0124-X", 5290, desc="Рычаг передний нижний без шаровой")],
    ("48657-28010", "TOYOTA"): [offer("TOYOTA", "48657-28010", 760)],
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
        return [{"brand": "TOYOTA"}] if number.startswith(("04", "48")) else [{"brand": "VAG"}]

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
    assert "на машину нужно 2 шт." in r["text"].lower() and "9 020 ₽" in r["text"]
    assert "Ford Focus CB8, 2012 г., 1.6 л 123 л.с." in r["text"]
    # Аналоги — по сроку: сначала что привезём быстрее
    shown_days = [a["days"] for a in o["analogs"][:3]]   # показанные клиенту — по сроку; скрытые идут за ними
    assert shown_days == sorted(shown_days)


def test_hub_without_side_asks_front_or_rear():
    p = run("X9FKXXEEBKCB57566 подшипник ступицы")["positions"][0]
    assert p["status"] == "choose" and p["question"] == "Нужен передний или задний?"
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
    assert got["воздушный фильтр"] == ["1848220"]   # существительное — у соседа «масляный фильтр»
    assert got["салонник"] == ["1709013"]
    pads = next(p for p in r["positions"] if p["query"] == "колодки передние")
    assert pads["status"] == "found" and "1900071" not in [v["oem"] for v in pads["variants"]]
    assert [v["alt"] for v in pads["variants"]] == [False, True]   # Motorcraft — тот же оригинал
    assert "1809256" not in r["text"]   # артикулы клиенту — только если включить в настройках


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
    assert list((folder / "trees").glob("FORD202201-*.json"))   # дерево — на машину, не на каталог
    fake2 = Fake()
    run("X9FKXXEEBKCB57566 масляный фильтр", Engine(fake2, folder))   # новый процесс — всё с диска
    # Ни машины, ни дерева, ни состава группы у Laximo больше не спрашиваем; цены — всегда свежие
    assert [c for c in fake2.calls if c[0] != "offers"] == []
    assert list((folder / "details" / "FORD202201").glob("*/2.json.gz"))

    class Down(Fake):
        async def laximo(self, method, params):
            raise RuntimeError("Laximo не отвечает")

    # Laximo лежит, а данные на диске устарели — всё равно отвечаем по сохранённому
    import app.podbor.catalog as C
    old = (C.TREE_TTL, C.VEHICLE_TTL, C.DETAILS_TTL)
    C.TREE_TTL = C.VEHICLE_TTL = C.DETAILS_TTL = -1
    try:
        r = run("X9FKXXEEBKCB57566 масляный фильтр", Engine(Down(), folder))
    finally:
        C.TREE_TTL, C.VEHICLE_TTL, C.DETAILS_TTL = old
    assert r["status"] == "ok" and r["positions"][0]["variants"][0]["oem"] == "1883037"


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
        assert c.post("/v1/podbor", json={"text": ""}).status_code == 422
        # Настройки ответа клиенту: артикулы включаются, пересборка — без нового подбора
        res = r.json()
        assert "2101656" not in res["text"]
        t = c.post("/v1/podbor/text", json={"result": res, "numbers": True, "analogs": 1})
        assert t.status_code == 200 and "Ford 2101656 (оригинал)" in t.json()["text"]
        assert c.post("/v1/podbor/text", json={"result": {"status": "ok"}}).status_code == 422
    off = create_app(replace(s, laximo_user=""), Abcp(s, transport=httpx.MockTransport(abcp)))
    with TestClient(off) as c:
        c.auth = auth
        assert c.post("/v1/podbor", json={"text": "X9FKXXEEBKCB57566 свечи"}).status_code == 503
    # Пароль не задан — страницы нет совсем
    closed = create_app(replace(s, podbor_password=""), Abcp(s, transport=httpx.MockTransport(abcp)))
    with TestClient(closed) as c:
        c.auth = auth
        assert c.get("/podbor").status_code == 404 and c.post("/v1/podbor", json={"text": "x" * 20}).status_code == 404


def test_client_text_sides_and_numbers():
    """Ответ клиенту: «Нужен передний или задний?», блоки по сторонам, «• Фирма — наименование — цена, срок»;
    артикулы — только с настройкой."""
    from app.podbor.engine import draft
    r = run("X9FKXXEEBKCB57566 подшипник ступицы")
    text = draft(r)
    assert "Нужен передний или задний?" in text
    # Спереди у Focus в группе и подшипник, и его ремкомплект — два варианта с названиями; сзади один
    assert "   Передний, вариант 1 — «Подшипник ступицы колеса» (на машину нужно 2 шт., цены за штуку):" in text
    assert "   Задний (на машину нужно 2 шт., цены за штуку):" in text
    assert "• Ford (оригинал) — Ступица колеса — 9 020 ₽" in text   # «Focus 2011-> задний» поставщика — ни о чём
    assert "• Ford (оригинал) — " in text and "2101656" not in text and "2215574" not in text
    with_numbers = draft(r, numbers=True, analogs=1)
    assert "• Ford 2101656 (оригинал)" in with_numbers
    assert T.ask_axis("стойка стабилизатора") == "Нужна передняя или задняя?"
    assert T.ask_axis("колодки") == "Нужны передние или задние?"
    assert T.side_label("крыло", "front", "left") == "Переднее левое"


def test_nice_name_cleans_supplier_text():
    from app.podbor.engine import nice_name
    assert nice_name("ПОДШИПНИК СТУПИЦЫ ПЕРЕДНЕЙ FAG 713679190", "FAG", "713679190") == "Подшипник ступицы передней"
    assert len(nice_name("Очень " * 30)) <= 61


def test_small_parts_are_not_analogs():
    from app.podbor.offers import not_the_part
    assert not_the_part("Ремкомплект передних тормозных колодок alfa romeo", "Комплект торм. колодок суппорта")
    assert not not_the_part("Колодки тормозные дисковые к-т", "Комплект торм. колодок суппорта")
    assert not not_the_part("Датчик положения коленвала", "Датчик положения коленвала")


def test_shared_noun_and_politeness():
    from app.podbor.engine import share_noun
    stop = frozenset()
    assert share_noun(["2 впускных", "2 выпускных клапана"], stop) == ["2 впускных клапана", "2 выпускных клапана"]
    assert share_noun(["масляный фильтр", "воздушный", "салонник"], stop)[1] == "воздушный фильтр"
    assert T.side_label("ГБЦ", "", "left") == "Левая"
    # «можно узнать цену и сроки» — не позиция
    r = run("X9FKXXEEBKCB57566 Здравствуйте можно узнать цену и сроки, масляный фильтр")
    assert [p["query"] for p in r["positions"]] == ["масляный фильтр"]


def test_original_with_leading_zero_and_english_names():
    from app.podbor.engine import base_name, ru_name
    o = curate([offer("CHRYSLER", "04892562AA", 900), offer("DAYCO", "6PK1", 500)], "4892 562AA", "CHRYSLER", set())
    assert o["original"] and o["original"]["price"] == 900
    assert ru_name(base_name("BELT, ALTERNATOR AND A/C COMPRESSOR")) == "Ремень генератора и кондиционера"
    assert ru_name(base_name("BELT, POWER STEERING")) == "Ремень ГУР"


def test_chat_shortcuts_and_oil_question():
    """Из переписки: «с/блок», «ш/о», «п/о», «к-т» — сокращения, а не разделители; «масло» без уточнения — спросить."""
    assert T.split_pieces("С/блок внутренний зад. ниж. рычага") == ["сайлентблок внутренний зад. ниж. рычага"]
    assert T.split_pieces("ш/о нижняя, к-т колодок") == ["шаровая опора нижняя", "комплект колодок"]
    assert T.parse("X9FKXXEEBKCB57566 г/ц сцепления").chunks == ["главный цилиндр сцепления"]
    from app.podbor.engine import Engine
    e = Engine(Fake(), None, set())
    ask = {a["text"] for a in e.ask if all(any(T.same(w, s) for s in T.stems("масло")) for w in a["query"])}
    assert any("Какое масло" in t for t in ask)


def test_typo_fix_keeps_real_words():
    """Опечатки правим («подшибник»), но не обычные слова: «готов» ≠ «голов», «котор» ≠ «мотор» (первая буква)."""
    from app.podbor.catalog import TreeIndex
    tree = node(0, "x", link=False, children=[node(1, "Подшипник ступичный"), node(2, "Головка блока цилиндров"),
                                               node(3, "Мотор печки"), node(4, "Граната")])
    ix = TreeIndex(tree, frozenset(), None, None, frozenset({T.stem("Гранта")}))
    assert ix.fix(T.stem("подшибник")) == T.stem("подшипник")
    assert ix.fix(T.stem("который")) == T.stem("который")      # не «мотор»: первая буква другая
    assert ix.fix(T.stem("Гранта")) == T.stem("Гранта")        # Лада Гранта — не «граната»


def test_neighbors_context_gearbox_and_ssangyong():
    from app.podbor.catalog import TreeIndex, Vehicle
    from app.podbor.engine import draft
    e = Engine(Fake(), None, set())
    tree = TreeIndex(node(0, "x", link=False, children=[
        node(15, "Колодки тормозные"), node(544, "Диск тормозной"), node(514, "Диски"),
        node(2, "Фильтр масляный"), node(3, "Фильтр воздушный", "воздухан"), node(137, "Насос масляный")]),
        e.stop, None, [{"words": ["воздухан"], "groups": [3]}])
    got = [q for q, _ in e.split(["передние колодки и диски, спасибо"], tree)]
    assert got == ["передние колодки", "диски тормозные"]          # рядом с колодками — тормозные
    got = [q for q, _ in e.split(["воздухан, масляный"], tree)]
    assert got[1] == "масляный фильтр"                              # существительное из группы соседа
    # Код коробки VAG («LKS») — не повод писать «МКПП»: это мог быть вариатор
    car = Vehicle("AU", "0", "s", "AUDI", "A4/Avant", {"manufactured": "2010", "transmission": "LKS(SA)"})
    assert "КПП" not in car.short() and "вариатор" in Vehicle("AU", "0", "s", "AUDI", "A4",
                                                               {"transmission": "multitronic"}).short()
    # SsangYong российской сборки: просим корейский VIN
    res = {"status": "vehicle_not_found", "request": {"ident": "Z8UA0B1SSBP036638", "model": "SsangYong Kyron",
                                                      "chunks": []}}
    assert "корейский VIN" in draft(res)


# ---------- разговор: следующие сообщения без VIN ----------

def test_dialog_side_choice_and_cheaper():
    e = Engine(Fake(), None, set())
    first = run("XW7BF4FK30S064389 колодки передние", e)
    assert first["memory"]["ident"] == "XW7BF4FK30S064389" and len(first["memory"]["positions"]) == 1
    rear = run("а задние?", e, memory=first["memory"])
    assert rear["status"] == "ok" and rear["followup"]
    assert [v["oem"] for v in rear["positions"][0]["variants"]] == ["04466-33180"]
    assert not rear["text"].startswith("Здравствуйте")   # продолжение — без приветствия
    assert len(rear["memory"]["positions"]) == 2         # передние помним: «а задние» — это ещё одна позиция
    # «давайте первый» — про последний ответ (задние), первая строка — оригинал
    pick = run("давайте первый", e, memory=rear["memory"])
    assert pick["status"] == "order"
    assert [(x["offer"]["number"], x["offer"]["price"]) for x in pick["reply"]["picks"]] == [("04466-33180", 7000)]
    assert "Оформляем" in pick["text"] and "Итого: 7 000 ₽" in pick["text"]
    cheap = run("дешевле нет?", e, memory=rear["memory"])
    assert cheap["status"] == "answer" and "самый недорогой" in cheap["text"] and "700 ₽" in cheap["text"]
    # Цена называет вариант: «за 9000» — передние, оригинал
    by_price = run("беру передние за 9000", e, memory=rear["memory"])
    assert [x["offer"]["number"] for x in by_price["reply"]["picks"]] == ["04465-33471"]
    vague = run("Заказывайте", e, memory=rear["memory"])
    assert vague["status"] == "order" and not vague["reply"]["picks"] and "какой вариант" in vague["text"]
    other = run("До скольки работаете?", e, memory=rear["memory"])
    assert other["status"] == "chat" and other["text"] == ""


def test_dialog_answer_to_question_replaces_position():
    e = Engine(Fake(), None, set())
    first = run("X9FKXXEEBKCB57566 подшипник ступицы", e)
    assert first["positions"][0]["question"]
    ans = run("задний", e, memory=first["memory"])
    assert [v["oem"] for v in ans["positions"][0]["variants"]] == ["2101656"]
    assert len(ans["memory"]["positions"]) == 1   # ответ на вопрос заменяет позицию, а не добавляет
    # Новый VIN посреди разговора — новая машина, старые позиции забыты
    fresh = run("XW7BF4FK30S064389 колодки задние", e, memory=ans["memory"])
    assert fresh["memory"]["ident"] == "XW7BF4FK30S064389" and len(fresh["memory"]["positions"]) == 1


def test_brand_reviews_client_sees_only_good():
    from app.podbor import reviews as R
    from app.podbor.engine import _offer_line
    R._table.cache_clear()
    good = R.find("LYNXauto", ["свечи зажигания"])
    assert good and good["category"] == "свеча зажигания" and R.public(good)["client"]
    weak = R.find("Zekkert", ["шаровая опора нижняя"])   # 1 хороший из 3 — клиенту не показываем
    assert weak and not R.public(weak)["client"]
    assert R.find("Aisin", ["помпа"])["category"] == "насос водяной"   # «помпа» = «насос водяной»
    o = {"brand": "LYNXauto", "number": "SP1", "description": "Свеча зажигания", "price": 400, "days": 1, "tags": []}
    assert "хорошие отзывы владельцев" in _offer_line(dict(o, reviews=R.public(good)), False)
    assert "отзывы" not in _offer_line(dict(o, reviews=R.public(weak)), False)
    R._table.cache_clear()


def test_brand_reviews_need_whole_category():
    from app.podbor import reviews as R
    R._table.cache_clear()
    r = R.find("Stellox", ["свечи зажигания", "Свеча зажигания"])
    assert r is None or r["category"] != "свеча накаливания"
    R._table.cache_clear()


def test_dialog_memory_answers_and_handoff():
    e = Engine(Fake(), None, set())
    first = run("XW7BF4FK30S064389 колодки задние", e)
    mem = first["memory"]
    stock = run("в наличии есть?", e, memory=mem)   # у всех 4 дня — в наличии нет, говорим самый быстрый
    assert stock["status"] == "answer" and "в наличии нет, быстрее всего" in stock["text"]
    more = run("а какие еще есть?", e, memory=mem)  # показали три аналога из четырёх — четвёртый
    assert more["status"] == "answer" and "ещё варианты" in more["text"] and "703 ₽" in more["text"]
    both = run("Давайте первый. Куда перевести деньги?", e, memory=mem)
    assert both["status"] == "order" and both["handoff"] == ["Куда перевести деньги?"]
    # Статус и визит — не новая деталь, даже если названа деталь
    assert run("Подскажите, датчик не пришёл ещё?", e, memory=mem)["status"] == "chat"
    assert run("Хорошо, давайте этот вариант", e, memory=mem)["status"] == "order"
    new = run("А расходомер воздуха?", e, memory=mem)
    assert new["status"] in ("ok", "chat")   # в маленьком тестовом дереве его может не быть — главное, не заказ


def test_laximo_usage_counted_and_kept():
    """Каждый запрос к Laximo считается по дням и методам и переживает перезапуск."""
    from dataclasses import replace

    import httpx

    from app.config import Settings
    from app.laximo import Laximo
    folder = tempfile.mkdtemp(prefix="lx-usage-")
    s = Settings(abcp_host="https://abcp.test", admin_login="a", admin_md5="a" * 32, token_secret=b"s" * 40,
                 laximo_user="u", laximo_pass="p", state_dir=folder)
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    lx = Laximo(s, transport=ok)
    asyncio.run(lx.call("findVehicle", {"identString": "X"}))
    asyncio.run(lx.call("listQuickGroup", {}))
    asyncio.run(lx.call("listQuickGroup", {}))
    rep = Laximo(s, transport=ok).usage.report()   # новый процесс — счёт с диска
    assert rep["today"] == 3 and rep["month"] == 3
    assert rep["month_by_method"] == {"findVehicle": 1, "listQuickGroup": 2}


def test_kinds_and_rules_confidence():
    from app.podbor.engine import kind_of
    from app.podbor.dialog import _sure, from_llm
    from app.podbor.offers import not_the_part
    assert kind_of(T.stems("масло моторное 5w30")) == "fluid" and kind_of(T.stems("масляный фильтр")) == "part"
    assert kind_of(T.stems("аккумулятор")) == "battery" and kind_of(T.stems("резина зимняя")) == "tire"
    assert kind_of(T.stems("резинка двери")) == "part"
    # Правилам — только короткое и ясное; отсрочка рядом с выбором, вопрос, несколько вещей — модели
    assert _sure({"kind": "pick", "reply": {"picks": [1]}}, "за 1410 закажите")
    assert not _sure({"kind": "pick", "reply": {"picks": [1]}}, "От Соренто по креплениям подходит?")
    assert not _sure({"kind": "chat"}, "Тяги давайте зекерт, остальное подумаю")
    assert _sure({"kind": "chat"}, "Подумаю")
    assert not _sure({"kind": "new"}, "А масло моторное и аккумулятор посмотрите")
    assert not_the_part("С/блок задний перед. рычага Toyota Camry", "Рычаг передней подвески")
    # «2 штуки», когда вариантов несколько, — не выбор первого, а вопрос какой
    mem = {"positions": [{"query": "колодки", "turn": 1, "variants": [{"name": "Колодки", "oem": "1", "brand": "TOYOTA",
           "axis": "", "lr": "", "alt": False, "offers": {"original": {"brand": "TOYOTA", "number": "1", "price": 9000,
           "days": 5, "tags": []}, "analogs": [{"brand": "Zekkert", "number": "Z", "price": 1500, "days": 1, "tags": []}],
           "stats": {}}}]}]}
    d = {"picks": [{"p": 1, "v": 1, "qty": 2}], "asks": [], "refine": [], "new_parts": [], "manager": [], "clarify": "",
         "unsure": False}
    r = from_llm(d, mem, 3, "2 штуки")["reply"]
    assert not r["picks"] and r["unclear"] == ["колодки"]
    assert from_llm(d, mem, 3, "давайте за 9000")["reply"]["picks"]


def test_left_and_right_under_one_number_are_two_pieces():
    """Наконечник Lexus RX: «Левый» и «Правый» — один номер. Это одна деталь на обе стороны, на машину две."""
    p = run("XW7BF4FK30S064389 рычаг передний нижний")["positions"][0]
    v = p["variants"][0]
    assert p["status"] == "found" and len(p["variants"]) == 1 and v["lr"] == "" and v["pair"] and v["amount"] == "2"
    from app.podbor.engine import draft
    assert "левый и правый одинаковые, на машину нужно 2 шт." in draft(run("XW7BF4FK30S064389 рычаг передний нижний")).lower()


def test_bushings_by_arm_number():
    """Сайлентблоков рычага Toyota в каталоге нет — берём сайлентблоки из кроссов номера рычага, сам рычаг — нет."""
    res = run("XW7BF4FK30S064389 сайлентблоки переднего рычага")
    v = res["positions"][0]["variants"][0]
    assert v["name"] == "Сайлентблок рычага" and v["offers"]["original"] is None
    assert {a["brand"] for a in v["offers"]["analogs"]} == {"Masuma", "Zekkert"}
    from app.podbor.engine import draft
    text = draft(res)
    assert "не продаёт" in text and "цену и срок уточним" not in text


def test_price_outliers_are_dropped():
    rows = [offer("CTR", "CE1", 50)] + [offer(b, f"N{i}", 600 + 50 * i) for i, b in enumerate(["A", "B", "C", "D", "E"])]
    o = curate(rows, "X", "TOYOTA", set())
    assert "CTR" not in {a["brand"] for a in o["analogs"]} and o["stats"]["price_min"] == 600


def test_floating_bushing_words_and_carrier_name():
    from app.podbor.catalog import _rename
    assert "сайлентблоки задней цапфы" in T.expand("Задок: плавающие и втулки стабилизатора")
    assert T.expand("плавающий с/б") == "сайлентблок задней цапфы"
    assert T.expand("Полиуретан. сайл.блок задней подв").startswith("Полиуретан. сайлентблок")
    assert _rename("Крепление заднего моста, правый (в подсборе)", "4230448030") == "Задний кулак (цапфа), правый (в подсборе)"
    assert _rename("Крепление заднего моста", "1234") == "Крепление заднего моста"


def test_firm_question_narrowed_by_detail_word():
    """«А салонный зеккерт есть?» про позицию «Фильтра»: масляный Zekkert не показываем."""
    from app.podbor.dialog import _narrow_by_words

    def hit(var, desc):
        return ({}, {"var": {"name": var}, "offer": {"brand": "Zekkert", "description": desc}}, False)

    oil = hit("Масляный фильтр", "Фильтр масл. Audi A4 IV 07")
    cabin = hit("Фильтр. элемент", "Фильтр салон. уголь Audi A4 IV 07")
    assert _narrow_by_words([oil, cabin], "а салонный зеккерт есть?", frozenset(), ()) == [cabin]
    # слова детали нет — отвечаем по всем, как раньше
    assert _narrow_by_words([oil, cabin], "зеккерт есть?", frozenset(), ()) == [oil, cabin]
    # слово не совпало ни с одним предложением — не молчим
    assert _narrow_by_words([oil, cabin], "а воздушный зеккерт есть?", frozenset(), ()) == [oil, cabin]


def test_fitting_kit_is_not_the_pads():
    """«Комплект установочный/монтажный колодок» — крепёж, а не колодки: не «самый дешёвый» и не в заказ."""
    from app.podbor.offers import not_the_part
    name = "Колодки тормозные задние"
    assert not_the_part("Комплект установочный задн. торм. колодок Hyundai i30 I, II", name)
    assert not_the_part("Комплект монтажный тормозных колодок KIA HYUNDAI CEED IX35", name)
    assert not not_the_part("Комплект тормозных колодок задних", name)   # обычный комплект колодок
    assert not not_the_part("Колодки тормозные задние", name)
    # клиент сам просил монтажный комплект
    assert not not_the_part("Комплект монтажный тормозных колодок", "Монтажный комплект колодок")


def test_bitrix_export_masks_phones_and_skips_robot(monkeypatch):
    monkeypatch.setenv("BX", "https://example.invalid/rest/1/x/")
    monkeypatch.setattr("sys.argv", ["bitrix_export.py", "2026-01-01", "out.jsonl"])
    from app.podbor import bitrix_export as B
    for raw in ("t8 903 425-76-49", "звоните 903 425-76-49", "+7 (978) 123-45-67", "89781234567", "8 (8692) 12-34-56",
                "тел 978-123-45-67 жду"):
        out = B.clean(raw, set())
        assert "[тел]" in out, raw
        assert "425-76" not in out and "123-45" not in out and "1234567" not in out and "12-34-56" not in out, raw
    # VIN, артикулы и суммы остаются
    keep = "XWEFF242380003661 артикул 04892562AA сумма 8931.00 заказ 1164805"
    assert B.clean(keep, set()) == keep
    assert B.ROBOT.match("Отправлено роботом\nБлагодарим за заказ №1165020")


def test_review_judge(tmp_path):
    """Судья: разбирает новые реплики журнала, «не помог» — первыми, повторно не отправляет, мусор модели не пишет."""
    import asyncio
    import json
    from datetime import date
    from app.podbor import review as R

    day = date.today().isoformat()
    jr = tmp_path / "journal"
    jr.mkdir()
    rows = [
        {"type": "turn", "dialog": "a", "turn": 1, "t": f"{day}T10:00:00", "text": "колодки задние", "status": "ok",
         "answer": "Колодки задние — Zekkert комплект установочный 350 ₽", "car": "KIA Ceed"},
        {"type": "turn", "dialog": "a", "turn": 2, "t": f"{day}T10:01:00", "text": "давайте зекерт", "status": "order",
         "answer": "Оформляем: Zekkert 350 ₽"},
        {"type": "turn", "dialog": "b", "turn": 1, "t": f"{day}T10:02:00", "text": "масло", "status": "ok", "answer": "Масло…"},
        {"type": "feedback", "dialog": "b", "turn": 1, "good": False, "comment": "не то"},
        {"type": "turn", "dialog": "c", "turn": 1, "t": f"{day}T10:03:00", "text": "привет", "status": "chat", "answer": ""},
    ]
    (jr / f"{day}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nобрыв{", encoding="utf-8")

    class Fake:
        enabled = True

        def __init__(self):
            self.asked = []

        async def chat(self, system, user, max_tokens=0):
            self.asked.append(user)
            if "давайте зекерт" in user:
                return '```json\n{"verdict":"suspect","problems":[{"kind":"wrong_order","what":"оформлен комплект, не колодки"}]}```'
            if "масло" in user:
                return '{"verdict":"suspect","problems":[]}'   # подозрение без причины — не находка
            return "не смог разобрать"

    llm = Fake()
    stat = asyncio.run(R.run(jr, llm, days=1, limit=10))
    # «привет» без ответа не разбирается; «не помог» (масло) идёт первым
    assert stat["cases"] == 3 and stat["sent"] == 3 and stat["failed"] == 1 and stat["suspect"] == 1
    assert llm.asked[0].startswith("Сейчас клиент пишет:\nмасло") or "масло" in llm.asked[0]
    recs = [json.loads(x) for x in (jr / "review" / f"{day}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["verdict"] for r in recs] == ["ok", "suspect"] and recs[0]["human"] is False
    assert recs[1]["problems"][0]["kind"] == "wrong_order"
    # повторный запуск: разобранное не отправляем, непонятый ответ модели — попробуем ещё раз
    llm2 = Fake()
    stat2 = asyncio.run(R.run(jr, llm2, days=1, limit=10))
    assert stat2["cases"] == 1 and "колодки задние" in llm2.asked[0]
    text = R.report(jr, 1)
    assert "подозрений 1" in text and "оформлен комплект" in text and "не помог" in text


def test_brand_in_first_message_is_split_off(monkeypatch):
    """«Фильтр воздушный mann» в первой реплике: деталь ищем без фирмы, фирму проверяем отдельно."""
    from types import SimpleNamespace
    from app import brands as AB
    from app.podbor.engine import Engine, load_rules

    fake = AB.Brands({"brands": {"MANN-FILTER": ["mann", "манн"], "BMW": ["bmw", "бмв"]}})
    monkeypatch.setattr(AB, "get", lambda: fake)
    rules = load_rules()
    eng = Engine(SimpleNamespace(laximo=None), None, set(), rules)
    v = SimpleNamespace(brand="BMW")
    side = T.Side("", "")
    items, asks = eng._split_brands([("фильтр воздушный mann", side), ("свечи", side), ("колодки bmw", side),
                                     ("mann", side)], v)
    assert [q for q, _ in items] == ["фильтр воздушный", "свечи", "колодки bmw", "mann"]
    assert asks == [{"position": 0, "brand": fake.key("mann"), "word": "mann"}]   # фирма машины и одно слово — не запрос


def test_vin_rejects_garbage_words():
    """«HRETETLCTEGJLAT10» — склеенные слова с фото СТС — не VIN; настоящий VIN Amarok — VIN."""
    from app.podbor.text import find_ident
    assert find_ident("VIN: WV1ZZZ2HZB8005243") == "WV1ZZZ2HZB8005243"
    assert find_ident("HRETETLCTEGJLAT10") == ""
    assert find_ident("VIN: HRETETLCTEGJLAT10") == ""
    # настоящие: с буквой в хвосте (Kia Рио), европейские без контрольной цифры, американский с верной
    assert find_ident("Z94C241BALR15141B") == "Z94C241BALR15141B"
    assert find_ident("WAUZZZ8K9BA123456") == "WAUZZZ8K9BA123456"
    assert find_ident("1M8GDM9AXKP042788") == "1M8GDM9AXKP042788"
    assert find_ident("1M8GDM9A1KP042788") == ""   # США: контрольная цифра не сходится


def test_all_around_and_price_tail_and_adjective_word():
    from types import SimpleNamespace
    from app.podbor.dialog import replace_adj
    from app.podbor.engine import Engine, _PRICE_TAIL, load_rules
    side = T.Side("", "")
    # «Диски и колодки в круг»: перед и зад отдельно — сзади бывают барабаны
    got = Engine._all_around([("диски тормозные", side), ("колодки в круг", side)], around=True)
    assert [(q, s.axis) for q, s in got] == [("диски тормозные", "front"), ("диски тормозные", "rear"),
                                              ("колодки", "front"), ("колодки", "rear")]
    # без «в круг» ничего не делим, масло в том же сообщении — тоже
    assert Engine._all_around([("диски тормозные", side)], around=False) == [("диски тормозные", side)]
    assert [q for q, _ in Engine._all_around([("масло", side), ("колодки", side)], around=True)][:1] == ["масло"]
    assert _PRICE_TAIL.sub("", "шаровые сколько стоят?") == "шаровые"
    # признак, который уже в запросе, второй раз не дописываем («шаровые шаровые»)
    assert replace_adj("сайлент блоки шаровые", "шаровые нужны") == "сайлент блоки шаровые"


def test_curate_anti_cross_by_supplier_count():
    """Аналог — только если артикул предлагают ≥5 поставщиков; нет трёх таких — порог ступенчато понижается."""
    rows = [offer("ORIG", "OEM1", 1000, confirm=3)]
    rows += [offer(b, f"N{i}", 500 + i, confirm=6) for i, b in enumerate(("A", "B", "C"))]    # подтверждены
    rows += [offer("SOLO", "S1", 100, confirm=1), offer("PAIR", "P1", 120, confirm=2)]          # один и два поставщика
    o = curate(rows, "OEM1", "TOYOTA", set())
    assert sorted(a["brand"] for a in o["analogs"]) == ["A", "B", "C"] and o["stats"]["min_stars"] == 5
    # подтверждённых меньше трёх — понижаем: 5 → 3 → 2 → 1
    few = [offer("ORIG", "OEM1", 1000), offer("A", "N1", 500, confirm=6), offer("B", "N2", 510, confirm=3),
           offer("PAIR", "P1", 120, confirm=2), offer("SOLO", "S1", 100, confirm=1)]
    o = curate(few, "OEM1", "TOYOTA", set())
    assert o["stats"]["min_stars"] == 2 and sorted(a["brand"] for a in o["analogs"]) == ["A", "B", "PAIR"]
    # бренд с гарантией магазина порогу не подчиняется
    o = curate(rows, "OEM1", "TOYOTA", {"SOLO"})
    assert "SOLO" in [a["brand"] for a in o["analogs"]]


def test_curate_keeps_cheapest_among_shown_and_text_rules():
    """Самый дешёвый аналог (он же самый медленный) — среди трёх показанных, а не в «ещё вариантах»."""
    rows = [offer("ORIG", "OEM1", 1000)] + [offer(b, f"N{i}", 600 + i, hours=24 * (i + 1), confirm=6)
                                           for i, b in enumerate("ABCD")]
    rows.append(offer("SLOWCHEAP", "S1", 100, hours=24 * 9, confirm=6))
    o = curate(rows, "OEM1", "TOYOTA", set())
    assert "SLOWCHEAP" in [a["brand"] for a in o["analogs"][:3]]
    # «Комплект фильтров для ТО» — три фильтра, а не группа «Комплект» кузова
    assert T.expand("комплект фильтров для ТО") == "масляный фильтр, воздушный фильтр, салонный фильтр"
    assert T.expand("фильтры на техобслуживание") == "масляный фильтр, воздушный фильтр, салонный фильтр"
    assert T.expand("масло и фильтры") != "масляный фильтр, воздушный фильтр, салонный фильтр"


def test_pending_request_continues_after_vin():
    import asyncio
    from types import SimpleNamespace
    from app.podbor.engine import Engine, load_rules
    eng = Engine(SimpleNamespace(laximo=None), None, set(), load_rules())
    first = asyncio.run(eng.run("хорошие фильтры на машину"))
    assert first["status"] == "no_vin" and "фильтры" in first["memory"]["pending"]


def test_llm_arbiter_replaces_only_clearly_better_weak_result():
    """Слабый результат правил проверяется моделью; берём её, если группа оценена заметно выше."""
    import asyncio
    from types import SimpleNamespace
    from app.podbor.engine import Engine, load_rules

    class Fake:
        enabled = True

        async def chat(self, system, user, max_tokens=0):
            return '{"positions":[{"part":"подшипник подвесной карданного вала","kind":"part","axis":"","lr":""}],"questions":[]}'

    eng = Engine(SimpleNamespace(laximo=None), None, set(), load_rules(), llm=Fake())
    weak = {"query": "кольцо подвесного", "status": "found", "kind": "part", "note": "«Кольцо» уточним по каталогу отдельно",
            "groups": [{"name": "Прокладка форсунки", "score": 0.58}], "variants": [], "side": {}}
    strong = {"query": "колодки", "status": "found", "kind": "part", "note": "",
              "groups": [{"name": "Колодки тормозные", "score": 0.9}], "variants": [], "side": {}}
    assert eng._weak(weak) and not eng._weak(strong)

    async def position(v, tree, q, side, kind=""):
        return {"query": q, "status": "found", "kind": "part", "note": "", "variants": [{"oem": "X"}], "side": {},
                "groups": [{"name": "Подшипник подвесной", "score": 1.0}]}
    eng.position = position
    positions = [dict(weak), dict(strong)]
    asyncio.run(eng._arbitrate(None, None, positions))
    assert positions[0]["query"] == "кольцо подвесного" and positions[0]["llm_part"].startswith("подшипник подвесной")
    assert positions[1]["query"] == "колодки" and "llm_part" not in positions[1]
    # результат модели не лучше — остаётся результат правил
    async def worse(v, tree, q, side, kind=""):
        return {"query": q, "status": "found", "kind": "part", "note": "", "variants": [], "side": {},
                "groups": [{"name": "Что-то", "score": 0.6}]}
    eng.position = worse
    positions = [dict(weak)]
    positions[0]["note"] = ""
    asyncio.run(eng._arbitrate(None, None, positions))
    assert "llm_part" not in positions[0]


def test_lost_head_with_weak_group_is_not_shown():
    from types import SimpleNamespace
    from app.podbor.engine import Engine, load_rules
    eng = Engine(SimpleNamespace(laximo=None), None, set(), load_rules())
    lost = {"query": "кольцо подвесного", "status": "found", "kind": "part", "note": "«Кольцо» уточним по каталогу отдельно, ниже — «X»",
            "groups": [{"name": "Прокладка форсунки", "score": 0.58}], "variants": [{"oem": "1"}], "question": "", "side": {}}
    fine = dict(lost, query="сайлентблок", groups=[{"name": "Втулка рычага", "score": 0.8}])
    ps = [lost, fine]
    eng._drop_lost(ps)
    assert ps[0]["status"] == "not_found" and ps[0]["variants"] == [] and ps[1]["variants"] == [{"oem": "1"}]


def test_note_diffs_show_only_sizes_and_trim():
    from app.podbor.engine import note_diffs
    v1 = {"name": "Тормозной диск", "note": "Тормозной диск (вентилир.);314X25MM 5/112", "unit_note": "ATE 1LT"}
    v2 = {"name": "Тормозной диск", "note": "300x12 5/112", "unit_note": "TRW-GIRLING 1KW"}
    assert note_diffs([v1, v2]) == ["314x25mm", "300x12"]
    assert note_diffs([v1, dict(v1)]) == ["", ""]                       # одинаковые — нечего показывать
    assert note_diffs([{"name": "А", "note": "VALUE PARTS"}, {"name": "Б", "note": ""}]) == ["", ""]   # шум не показываем


def test_shadow_compare_and_report(tmp_path):
    import json
    from datetime import date
    from app.podbor import review as R
    from app.podbor.shadow import compare
    res = {"status": "ok", "positions": [{"query": "колодки передние", "kind": "part", "groups": [{"name": "Колодки тормозные"}],
                                          "variants": [{"name": "Колодки тормозные передние"}]}]}
    same = {"positions": [{"part": "колодки тормозные передние", "kind": "part"}]}
    assert compare(same, res)["agree"] is True
    other = {"positions": [{"part": "пыльник шруса", "kind": "part"}]}
    d = compare(other, res)
    assert d["agree"] is False and d["llm_not_in_rules"] == ["пыльник шруса"]
    assert compare(None, res) is None and compare(same, {"status": "no_vin", "positions": []}) is None
    # запись и отчёт
    jr = tmp_path / "journal"
    jr.mkdir()
    day = date.today().isoformat()
    rec = {"t": f"{day}T10:00:00", "type": "shadow", "dialog": "d1", "turn": 1, "agree": False, "rules": ["кольцо подвесного"],
           "llm": ["подшипник подвесной"], "llm_not_in_rules": ["подшипник подвесной"]}
    (jr / f"{day}.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    text = R.shadow_report(jr, 1)
    assert "расхождений 1" in text and "кольцо подвесного" in text and "подшипник подвесной" in text


def test_llm_arbiter_rescues_not_found_only_with_confident_result():
    import asyncio
    from types import SimpleNamespace
    from app.podbor.engine import Engine, load_rules

    class Fake:
        enabled = True

        async def chat(self, system, user, max_tokens=0):
            return '{"positions":[{"part":"замок капота","kind":"part","axis":"","lr":""}],"questions":[]}'

    eng = Engine(SimpleNamespace(laximo=None), None, set(), load_rules(), llm=Fake())
    gone = {"query": "кожух замка капота", "status": "not_found", "kind": "part", "note": "", "groups": [], "variants": [],
            "question": "", "side": {}}
    noise = dict(gone, query="завтра подъеду") | {"kind": "chemistry"}   # не деталь — модель не зовём
    assert eng._weak(gone) and not eng._weak(noise)

    async def good(v, tree, q, side, kind=""):
        return {"query": q, "status": "found", "kind": "part", "note": "", "variants": [{"oem": "X"}], "side": {},
                "groups": [{"name": "Замок капота", "score": 1.0}]}

    async def weak_alt(v, tree, q, side, kind=""):
        return dict(await good(v, tree, q, side), groups=[{"name": "Что-то", "score": 0.4}])
    eng.position = good
    ps = [dict(gone)]
    asyncio.run(eng._arbitrate(None, None, ps))
    assert ps[0]["status"] == "found" and ps[0]["query"] == "кожух замка капота" and ps[0]["llm_part"] == "замок капота"
    eng.position = weak_alt
    ps = [dict(gone)]
    asyncio.run(eng._arbitrate(None, None, ps))
    assert ps[0]["status"] == "not_found"   # неуверенную находку не берём
