"""Бренд оригинала для марки машины.

Основной источник — полный справочник брендов ABCP (app/brands.py): «Skoda» там алиас «VAG»,
«Kia» — алиас «Hyundai-KIA», «Lexus» — «TOYOTA», а группа FORD = FORD + MOTORCRAFT.
Полный справочник в публичный репозиторий не кладём, поэтому выжимку по маркам машин
храним в app/data/oem_brands.json — на ней подбор работает и без него (тесты, CI).

Обновить выжимку после новой выгрузки брендов:
    python -m app.brands report.xls && python -m app.podbor.brands
"""
import json
import sys
from pathlib import Path

from .. import brands as AB

FILE = AB.DATA / "oem_brands.json"

# Марки, которые встречаются в Laximo и у покупателей. Новая марка — дописать и перегенерировать.
MAKES = [
    "ACURA", "ALFA ROMEO", "AUDI", "BAIC", "BMW", "BYD", "CADILLAC", "CHANGAN", "CHERY", "CHEVROLET",
    "CHRYSLER", "CITROEN", "CUPRA", "DACIA", "DAEWOO", "DAIHATSU", "DATSUN", "DODGE", "DONGFENG", "DS",
    "EXEED", "FAW", "FIAT", "FORD", "GAZ", "GEELY", "GENESIS", "GREAT WALL", "HAVAL", "HONDA", "HYUNDAI",
    "INFINITI", "ISUZU", "IVECO", "JAC", "JAECOO", "JAGUAR", "JEEP", "JETOUR", "KIA", "LADA", "LAND ROVER",
    "LEXUS", "LIFAN", "LIVAN", "MAZDA", "MERCEDES-BENZ", "MINI", "MITSUBISHI", "MOSKVICH", "NISSAN",
    "OMODA", "OPEL", "PEUGEOT", "PORSCHE", "RAVON", "RENAULT", "ROVER", "SAAB", "SEAT", "SKODA",
    "SMART", "SSANGYONG", "SUBARU", "SUZUKI", "TANK", "TESLA", "TOYOTA", "UAZ", "VAZ", "VOLKSWAGEN",
    "VOLVO", "VORTEX", "VW", "ZAZ", "ZOTYE",
]
# Чего нет в группах ABCP, но это тот же производитель: FOMOCO — Ford Motor Company
EXTRA_FAMILY = {"FORD": ["FOMOCO"]}


def build(full: AB.Brands) -> dict:
    makes = {m: full.canon(m) for m in MAKES if full.canon(m) != m or AB.norm(m) in full.alias}
    families = {}
    for brand in sorted(set(makes.values())):
        g = full.group_of.get(full.key(brand))
        fam = list(dict.fromkeys((full.groups.get(g) if g else [brand]) + EXTRA_FAMILY.get(AB.norm(brand), [])))
        if len(fam) > 1:
            families[brand] = fam
    return {"_": "Выжимка справочника брендов ABCP по маркам машин: python -m app.podbor.brands",
            "makes": makes, "families": families, "extra": EXTRA_FAMILY}


def load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"makes": {}, "families": {}, "extra": EXTRA_FAMILY}


if __name__ == "__main__":
    full = AB.get()
    if not full.loaded:
        sys.exit("Нет справочника брендов: сначала python -m app.brands report.xls")
    data = build(full)
    Path(FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data['makes'])} марок, {len(data['families'])} групп → {FILE}")
