"""Знания о машинах — в data/podbor_knowledge.json, а не в коде робота (аудит 26.10.2026)."""
import ast
import re
from pathlib import Path

from app.podbor import catalog as C, differ as DF, engine as E, knowledge as K, makes as MK, offers as O, wizard as W

APP = Path(__file__).resolve().parents[1] / "app"
CODE = [APP / "podbor", APP / "assistant"]   # робот и Кирилл
# Сборщики выжимок и лабораторные скрипты: списки марок там — что выгружать и на чём мерить, а не знания робота
NOT_ROBOT = {"brands.py", "fleet.py", "lab.py"}
CARS = re.compile(r"тойот|лексус|хенд[аэе]|хунда|хёнд|хьюнда|(?<![а-я])ки[ая](?![а-я])|фольксваген|фольц|шкод|(?<![а-я])ауди|"
                  r"ниссан|мицубис|митсубис|(?<![а-я])рено(?![а-я])|пежо|ситро|опел|шевроле|бмв|мерседес|(?<![а-я])форд|мазд|"
                  r"хонд|субару|сузуки|вольво|сангйонг|санг\s*йонг|саньен|ssang|toyota|lexus|hyundai|\bkia\b|volkswagen|"
                  r"\bvag\b|skoda|\baudi\b|nissan|mitsubishi|renault|peugeot|citroen|\bopel\b|chevrolet|\bbmw\b|mercedes|"
                  r"\bford\b|mazda|honda|subaru|suzuki|volvo|kyron|korando|daewoo", re.I)


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def test_no_car_names_in_robot_code():
    """Марка или модель в строке кода — это знание о машине; ему место в данных. В промптах Кирилла — тоже: знание
    мастера («плавающие» — цапфа, у Toyota цапфа в узле амортизатора) — запись в разделе notes, а не строчка в промпте.
    В комментариях и описаниях функций примеры марок можно и нужно: они объясняют, откуда взялось общее правило."""
    found = []
    for f in sorted(p for d in CODE for p in d.glob("*.py")):
        if f.name in NOT_ROBOT:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                m = CARS.search(node.value)
                if m:
                    found.append(f"{f.name}:{node.lineno} «{m.group(0)}» в {node.value[:60]!r}")
    assert not found, "Знания о машинах в коде — перенести в data/podbor_knowledge.json:\n" + "\n".join(found)


def test_knowledge_file_is_sound():
    # «\b» из heredoc в JSON становится символом «забой» (26.10, economy_lines): регулярка молча перестаёт совпадать
    raw = K.FILE.read_text(encoding="utf-8")
    assert not re.search(r"[\x00-\x08\x0b-\x1f]", raw) and "\\u000" not in raw and not re.search(r'[^\\]\\[bf]', raw)
    fams = K.families()
    assert fams["toyota"] == ("Toyota/Lexus", ("TOYOTA", "LEXUS"))
    assert set(K.family_words()) <= set(fams)
    for rx, num, to in K.renames():
        assert to and rx.pattern and num.pattern
    for ident, words, text in K.vin_hints():
        assert text
    for r in K.rules("wizard_brands"):
        re.compile(r["words"])
    assert all(isinstance(v, list) for v in K.table("wmi_brands").values())
    assert all(isinstance(v, str) and v.isupper() for v in K.table("part_brands_ru").values())


def test_knowledge_reaches_robot():
    """Таблицы, что раньше были в коде, на месте: робот читает их из данных."""
    assert C._rename("Крепление заднего моста, правый (в подсборе)", "42304-48050").startswith("Задний кулак")
    assert C._rename("Крепление заднего моста", "12345") == "Крепление заднего моста"   # номер не тот — не трогаем
    assert MK.mismatch("шайбы форсунок сан енг Кайрон", "BMW")[1] == "ssangyong"
    assert W.brand_of("шкода карок 2021") == "SKODA" and W.wmi_brands("XW8ZZZ") == ["VOLKSWAGEN", "SKODA"]
    kia = E.Vehicle(catalog="KIA", vehicle_id="1", ssd="", brand="KIA", name="PICANTO", attrs={})
    assert E._without_car("свечи для киа пиканто", kia).strip() == "свечи"
    assert DF.economy_line("JZW615301N", "Тормозн.диск") == {"line": "VAG Economy", "maker": "VAG"}
    assert O.oem_brand_for("Неведомая") == "Неведомая"
    assert {"HYUNDAIKIA", "MOBIS"} <= O.original_keys("Kia")


def test_master_notes_only_where_they_apply():
    """Знания мастера попадают в промпт, только если к месту: слова клиента и марка машины (26.10, руководитель,
    Lexus RX330: «задок плавающие» — Кирилл дал сайлентблоки рычагов; подсказка про цапфу Toyota — только Toyota/Lexus)."""
    dec = K.notes("decoder", "Задок плавающие, стабы и шаровые", "LEXUS")
    assert any("плавающие" in n and "цапфы" in n for n in dec) and any("шаровая опора" in n for n in dec)
    assert not any("стабилизатор" in n for n in K.notes("decoder", "стабилизатор задний", ""))   # полное слово — не жаргон
    # 27.10, замер расшифровщика: «аморики» без подсказки ушли в вопрос; полное «амортизаторы» — не жаргон
    assert all(any("амортизатор" in n for n in K.notes("decoder", w, "")) for w in ("аморики", "аморты по кругу", "амор"))
    assert not K.notes("decoder", "амортизаторы по кругу", "")
    assert any("цапф" in n for n in K.notes("selector", "сайлентблоки плавающие задней цапфы", "LEXUS"))
    assert not K.notes("selector", "сайлентблоки плавающие задней цапфы", "KIA")    # не та марка — молчим
    assert not K.notes("decoder", "колодки передние", "TOYOTA")                       # нет слов — промпт не растёт
    assert K.family("Lexus") == "toyota" and K.family("Mercedes-Benz") == "mercedes" and K.family("") == ""
    for r in K.rules("notes"):
        assert r["for"] in ("decoder", "selector") and r["text"] and r["why"] and r["added"]
        assert set(r.get("makes") or ()) <= set(K.families()), r


def test_kirill_prompts_get_notes():
    from app.assistant import core
    assert "{notes}" in core.DECODER and "{notes}" in core.SELECTOR
    assert "Знания мастера" in core._notes("decoder", "плавающие", "LEXUS") and core._notes("decoder", "фильтр", "") == ""


def test_journal_keeps_why():
    """Журнал хранит «почему» Кирилла рядом с позицией — страница «Как выбирал» (27.10)."""
    from app.podbor.journal import brief
    why = {"decoder": "«плавающие» — сайлентблоки цапфы", "selector": "взял из узла цапфы", "notes": ["n"]}
    res = {"status": "assistant:ok", "positions": [{"query": "Сайлентблок цапфы", "status": "found", "why": why,
                                                     "group": "Задняя подвеска"}]}
    p = brief(res)["positions"][0]
    assert p["why"] == why and p["group"] == "Задняя подвеска"
    assert "why" not in brief({"positions": [{"query": "колодки", "status": "found"}]})["positions"][0]
