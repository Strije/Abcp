"""Знания о машинах — в data/podbor_knowledge.json, а не в коде робота (аудит 26.10.2026)."""
import ast
import re
from pathlib import Path

from app.podbor import catalog as C, differ as DF, engine as E, knowledge as K, makes as MK, offers as O, wizard as W

PODBOR = Path(__file__).resolve().parents[1] / "app" / "podbor"
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
    """Марка или модель в строке кода — это знание о машине; ему место в данных. В комментариях и описаниях функций
    примеры марок можно и нужно: они объясняют, откуда взялось общее правило."""
    found = []
    for f in sorted(PODBOR.glob("*.py")):
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
