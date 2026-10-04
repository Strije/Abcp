"""Бренды как в ABCP: алиасы («Fag» = «FAG», «LEMFÖRDER» = «LEMFORDER») и группы брендов (FORD + MOTORCRAFT).

Справочник — выгрузка «Бренды и алиасы» из панели ABCP (report.xls, ~19 тыс. брендов). Его держим
рядом с сервером, а не в публичном репозитории: STATE_DIR/brand_aliases.json на сервере
или app/data/brand_aliases.json локально (в .gitignore). Нет файла — работает короткий
встроенный список, как раньше.

Обновить после новой выгрузки (нужен xlrd):
    pip install xlrd && python -m app.brands report.xls [куда_сохранить.json]
"""
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
FILE_NAME = "brand_aliases.json"

# Если справочника нет: самые частые расхождения написаний (было в abcp.py)
BUILTIN = {"MANNFILTER": "MANN", "HYUNDAIMOBIS": "HYUNDAIKIA", "MOBIS": "HYUNDAIKIA",
           "GENERALMOTORS": "GM", "MERCEDESBENZ": "MERCEDES", "LEMFORDER": "LEMFOERDER"}


def norm(s) -> str:
    return re.sub(r"[^A-ZА-ЯЁ0-9]", "", str(s or "").upper())


class Brands:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.alias: dict[str, str] = {}      # нормализованное написание → название бренда в ABCP
        for name, aliases in (data.get("brands") or {}).items():
            for a in [name, *aliases]:
                self.alias.setdefault(norm(a), name)
        self.group_of: dict[str, str] = {}   # ключ бренда → название группы
        self.groups: dict[str, list[str]] = data.get("groups") or {}
        for g, members in self.groups.items():
            for m in members:
                self.group_of.setdefault(self.key(m), g)

    @property
    def loaded(self) -> bool:
        return bool(self.alias)

    def canon(self, name: str) -> str:
        """Как бренд называется в ABCP: «Volkswagen» → «VAG», «Lemf» → «LEMFORDER»."""
        return self.alias.get(norm(name), str(name or "").strip())

    def key(self, name: str) -> str:
        """Ключ для сравнения: одинаковый у всех написаний одного бренда."""
        k = norm(self.canon(name))
        return BUILTIN.get(k, k)

    def family(self, name: str) -> set[str]:
        """Ключи всех брендов группы (FORD → FORD, MOTORCRAFT); без группы — только сам бренд."""
        k = self.key(name)
        g = self.group_of.get(k)
        return {self.key(m) for m in self.groups.get(g, [])} | {k} if g else {k}


def _candidates() -> list[Path]:
    out = []
    if os.environ.get("BRANDS_FILE"):
        out.append(Path(os.environ["BRANDS_FILE"]))
    out.append(Path(os.environ.get("STATE_DIR", "/var/lib/avtodrug-api")) / FILE_NAME)
    out.append(DATA / FILE_NAME)
    return out


@lru_cache(maxsize=1)
def get() -> Brands:
    for p in _candidates():
        try:
            return Brands(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return Brands()


def from_xls(path: str) -> dict:
    import xlrd  # только для обновления справочника, серверу не нужен

    book = xlrd.open_workbook(path, ignore_workbook_corruption=True)  # выгрузка ABCP чуть битая
    brands: dict[str, list[str]] = {}
    sh = book.sheet_by_index(0)
    for r in range(1, sh.nrows):
        name, al = (str(x).strip() for x in sh.row_values(r)[:2])
        if name:
            brands[name] = [a.strip() for a in al.split(",") if a.strip()]
    groups: dict[str, list[str]] = {}
    sh = book.sheet_by_index(1)
    for r in range(1, sh.nrows):
        name, members = (str(x).strip() for x in sh.row_values(r)[:2])
        if name:
            groups[name] = [m.strip() for m in members.split(",") if m.strip()]
    return {"_": "Справочник брендов ABCP (выгрузка report.xls): python -m app.brands report.xls",
            "brands": brands, "groups": groups}


if __name__ == "__main__":
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA / FILE_NAME
    d = from_xls(sys.argv[1])
    out.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"{len(d['brands'])} брендов, {len(d['groups'])} групп → {out}")
