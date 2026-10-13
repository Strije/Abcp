"""Другие имена деталей полного каталога: как деталь зовёт покупатель («Холостой шкив» → «ролик», «JOINT KIT,FR AXLE
SHAFT» → «шрус»). Словарь собран моделью по 44 тыс. названий Laximo (13.10, lab/lexicon_pack.py). Служит только
дополнительными словами названия ВНУТРИ уже найденной группы: робот сравнивает их со словами клиента. Отдельным поиском
по каталогу словарь не работает — проба 13.10 так находила чужое (задний бампер → передний).

Файл — STATE_DIR/podbor/lexicon.json (выдержка из каталога Laximo, в публичный git не кладём; PODBOR_LEXICON — другой
путь). Нет файла — словаря нет, робот работает как раньше. Однословные названия («Втулка», «Манжета») в словарь не
попадают: без узла модель их угадывала («втулка жгута проводов»).
"""
import json
import os
import re
from pathlib import Path

from . import text as T

_STATE: dict = {}
_LR = r"\b(?:rh|lh|r|l|п|л|пр|лев|прав|правый|левый|правая|левая|правое|левое|правые|левые|right|left)\b"
_COLOR = (r"\b(?:ч[её]рн\w*|бел\w*|сер\w*|красн\w*|син\w*|black|white|grey|gray|silver|chrome|хром\w*|satin\w*|"
          r"[a-z]*schwarz\w*|grau\w*)\b")


def family_key(name: str) -> str:
    """Как у сборщика словаря: почти-дубли («… диаметр 77.555 мм», «… RH/LH», цвет, коды в скобках) — один ключ."""
    k = re.sub(r"\s+", " ", name or "").strip().lower()
    k = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", k)
    k = re.sub(r"[a-z0-9]*\d[a-z0-9-]*", " ", k)
    k = re.sub(_LR, " ", k)
    k = re.sub(_COLOR, " ", k)
    k = re.sub(r"[^a-zа-яё ]+", " ", k)
    return " ".join(w for w in k.split() if len(w) > 1) or k


def _load() -> dict[str, list[str]]:
    if "names" not in _STATE:
        path = Path(os.environ.get("PODBOR_LEXICON") or
                    Path(os.environ.get("STATE_DIR", "/var/lib/avtodrug-api")) / "podbor" / "lexicon.json")
        try:
            _STATE["names"] = json.loads(path.read_text(encoding="utf-8")).get("names", {})
        except (OSError, ValueError):
            _STATE["names"] = {}
    return _STATE["names"]


def _entry(name: str) -> list[list[str]]:
    """[главные слова, остальные слова покупателя] или [[], []] — нет в словаре."""
    names = _load()
    if not names or not name:
        return [[], []]
    k = re.sub(r"\s+", " ", name).strip().lower()
    e = names.get(k) or names.get(family_key(k)) or [[], []]
    return e if e and isinstance(e[0], list) else [list(e), []]


def heads(name: str) -> list[str]:
    """Главные слова покупателя для названия детали: «Холостой шкив» → [ролик]."""
    return _entry(name)[0]


def words(name: str) -> list[str]:
    """Все слова покупателя (главные и уточнения): «Холостой шкив» → [ролик, обводн, паразитн]."""
    h, w = _entry(name)
    return h + w


def name_stems(name: str, stop: frozenset[str] = frozenset()) -> list[str]:
    """Основы названия и, если их там нет, слова покупателя из словаря: «Холостой шкив» → [холост, шкив, ролик, обводн…]."""
    st = T.stems(name, stop)
    return st + [w for w in words(name) if w not in stop and not any(T.same(w, s) for s in st)]
