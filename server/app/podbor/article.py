"""Клиент назвал номер детали: «артикул 26209425906 подойдёт?», «Krauf ALB1689DD», «HYUNDAI | KIA 571003L100 — Насос ГУР».

Laximo не умеет «где применяется номер» в нашем шлюзе, поэтому проверяем через то, что есть:
поставщики говорят, что это за деталь («Насос ГУР»), каталог машины по VIN даёт оригинал этой детали, а его кроссы —
все номера, которые поставщики считают аналогами. Номер клиента среди них — подойдёт; нет — скорее всего нет,
и показываем, что подходит. Номер у поставщиков не известен — честно: проверим вручную.
"""
import re

from . import text as T

# Номер детали: буквы с цифрами или цифры от шести знаков; «-», «.» и пробелы внутри у поставщиков бывают
_TOKEN = re.compile(r"(?<![A-Za-zА-Яа-я0-9])([A-Za-z0-9][A-Za-z0-9.\-]{4,18}[A-Za-z0-9])(?![A-Za-zА-Яа-я0-9])")
_PRICE = re.compile(r"^\s*(?:р\b|руб|₽|шт|мм|mm|см|л\b|кг|г\.?\s|год)", re.I)
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.I)


def norm(number: str) -> str:
    """Как поставщики сравнивают номера: без «-», «.», пробелов и ведущих нулей."""
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper()).lstrip("0")


def find(text: str, limit: int = 3) -> list[str]:
    """Номера деталей в тексте клиента. Не номер: VIN, телефон, цена («3200р»), размер, год, «1.6л», «5W30»."""
    out: list[str] = []
    for m in _TOKEN.finditer(text or ""):
        raw = m.group(1).strip(".-")
        key = norm(raw)
        if len(key) < 5 or not re.search(r"\d", key) or _VIN.match(raw) or key in (norm(x) for x in out):
            continue
        if _PRICE.match(text[m.end():m.end() + 5]):
            continue
        digits = re.sub(r"\D", "", raw)
        if raw.isdigit() and (len(raw) < 6 or (len(raw) in (10, 11) and raw[:1] in "78")):   # годы, суммы, телефоны
            continue
        if re.fullmatch(r"\d+[.,]\d+", raw) or re.fullmatch(r"\d{1,2}[wW]\d{2}", raw):   # «1.6», «5W30»
            continue
        if len(digits) < 2:   # «XS-MAX»: номер без цифр почти не бывает; «Y27FER-C» (свеча Denso) — да
            continue
        out.append(raw)
        if len(out) >= limit:
            break
    return out


def what(rows: list[dict]) -> tuple[str, str]:
    """По «Вы искали» поставщиков: (фирма, что за деталь по-русски). Описание — самое частое русское."""
    descs = [str(r.get("description") or "").strip() for r in rows]
    ru = [d for d in descs if re.search(r"[а-яё]{4,}", d, re.I)]
    if not ru:
        return (str(rows[0].get("brand") or "") if rows else ""), ""
    best = max(set(ru), key=lambda d: (ru.count(d), -len(d)))
    brand = next((str(r.get("brand") or "") for r in rows if str(r.get("description") or "").strip() == best), "")
    return brand, best


def name_of(desc: str) -> str:
    """Название для поиска по каталогу из описания поставщика: «Насос ГУР Hyundai Grandeur 05-» → «насос гур»."""
    words = [w for w in T.words(desc) if re.fullmatch(r"[а-яё/-]{3,}", w.lower())]
    return " ".join(words[:4]).lower()


def fits(number: str, variants: list[dict]) -> dict | None:
    """Номер клиента — оригинал или среди аналогов одного из вариантов каталога: какой это вариант."""
    key = norm(number)
    for v in variants:
        own = norm(v.get("oem"))
        numbers = {norm(x) for x in ((v.get("offers") or {}).get("numbers") or [])}
        if key == own or key in numbers:
            return {"oem": v.get("oem"), "original": key == own, "name": v.get("name")}
    return None
