"""Отзывы владельцев о фирмах по видам деталей: «Zekkert — опора шаровая: 5 хороших из 6».

    python -m app.podbor.reviews import avito-otzyvy.xlsx     # лист «Бренд и категория» → data/brand_reviews.json

В репозиторий кладётся только сводка по фирме и виду детали (счёт отзывов, вывод, ссылка на источник) —
без авторов и текстов отзывов. Клиенту показываем только хорошее и только при 3+ размеченных отзывах:
по одному-двум отзывам о фирме судить нельзя. Менеджер на странице видит всё, включая жалобы.
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

from .. import brands as AB
from . import text as T

FILE = Path(__file__).resolve().parent.parent / "data" / "brand_reviews.json"
MIN_REVIEWS = 3      # меньше — не показываем клиенту и не учитываем в «какой лучше»
GOOD_SHARE = 0.7     # «хорошие отзывы» — от 70% хороших

# Как называют детали клиенты и каталог — и как в отзывах
_ALIAS = {"помп": "насос водяной", "лямбд": "датчик кислорода", "дпкв": "датчик положения коленвала",
          "шаров": "опора шаровая", "граната": "шрус наружный", "салонник": "фильтр салона",
          "воздухан": "фильтр воздушный", "бензонасос": "насос топливный", "подушк": "опора двигателя"}


def import_xlsx(path: str, out: Path = FILE):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb["Бренд и категория"].iter_rows(values_only=True))
    head = [str(h or "") for h in rows[0]]
    col = {name: head.index(name) for name in head}
    data = []
    for r in rows[1:]:
        if not r or not r[col["Бренд"]]:
            continue
        num = lambda k: int(float(r[col[k]] or 0))  # noqa: E731
        site = r[col["Рейтинг на сайте-источнике"]]
        data.append({"brand": str(r[col["Бренд"]]).strip(), "category": str(r[col["Категория"]]).strip().lower(),
                     "good": num("Хороших"), "bad": num("Плохих"), "mixed": num("По-разному"),
                     "total": num("Всего отзывов"), "site_rating": float(site) if site not in (None, "") else None,
                     "summary": str(r[col["Вывод по отзывам"]] or "").strip(),
                     "source": str(r[col["Источники"]] or "").split()[0] if r[col["Источники"]] else ""})
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(data)} строк → {out}")


@lru_cache(maxsize=1)
def _table() -> dict[str, list[dict]]:
    try:
        rows = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, list[dict]] = {}
    for r in rows:
        r["stems"] = T.stems(r["category"])
        out.setdefault(AB.get().key(r["brand"]), []).append(r)
    return out


def labeled(r: dict) -> int:
    return r["good"] + r["bad"] + r["mixed"]


def score(r: dict) -> float:
    """Доля хороших со сглаживанием: 1 хороший из 1 — не 100%, а 67%."""
    return (r["good"] + 0.5 * r["mixed"] + 1) / (labeled(r) + 2)


def _stems(texts: list[str]) -> list[str]:
    st = [s for t in texts for s in T.stems(t)]
    for k, v in _ALIAS.items():
        if any(s.startswith(k) for s in st):
            st += T.stems(v)
    return st


def find(brand: str, texts: list[str]) -> dict | None:
    """Отзывы о фирме по этому виду детали. Вид — по словам запроса, названия из каталога и группы:
    главное слово вида («опора» в «опора шаровая») должно совпасть, остальные — по большей части."""
    rows = _table().get(AB.get().key(brand))
    if not rows:
        return None
    st = _stems(texts)
    best, best_key = None, (0.0, 0)
    for r in rows:
        cs = r["stems"]
        if not cs or not any(T.same(T.head(cs), s) for s in st):
            continue
        share = sum(any(T.same(c, s) for s in st) for c in cs) / len(cs)
        key = (share, labeled(r))
        if share >= 0.5 and key > best_key:
            best, best_key = r, key
    return best


def public(r: dict) -> dict:
    """Что уходит на страницу: счёт, вывод, ссылка. Пометка для клиента — только хорошая и только при 3+ отзывах."""
    n = labeled(r)
    return {"category": r["category"], "good": r["good"], "bad": r["bad"], "mixed": r["mixed"], "total": r["total"],
            "site_rating": r["site_rating"], "summary": r["summary"], "source": r["source"],
            "score": round(score(r), 2), "client": n >= MIN_REVIEWS and r["good"] / n >= GOOD_SHARE}


def annotate(var: dict, texts: list[str]):
    """Каждому предложению варианта — отзывы о его фирме по этому виду детали, если есть."""
    o = var.get("offers")
    if not o:
        return
    names = texts + [var.get("name", "")]
    for x in ([o["original"]] if o.get("original") else []) + list(o.get("analogs") or []):
        r = find(str(x.get("brand") or ""), names)
        if r:
            x["reviews"] = public(r)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "import":
        import_xlsx(sys.argv[2])
    else:
        print(__doc__)
