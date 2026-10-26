"""Прайс собственных марок поставщика (СТМ Авторусь: GANZ, BOLK, ARNEZI) — фото деталей и применяемость.

Поставщик выкладывает прайс с фото для клиентов. Берём из него только то, чего нет у Laximo и ABCP: фото детали
и её вид словами торговли. Цен и остатков в выжимке нет (цена в прайсе — закупочная). Номера отсюда — не номера
для клиента: правило «номера только из Laximo и ABCP» остаётся.

    python -m app.podbor.stm price_stm.xlsx parts.json    # выжимка; на сервере — STATE_DIR/stm/parts.json, не в git

Фото к детали Кирилла (photo_for): та же деталь на эту машину — «фото этой детали»; на эту машину нет, а деталь
типовая (data/podbor_knowledge.json, photo_typical) — «так выглядит деталь, фото для примера»; иначе — без фото.
"""
import json
import re
import sys
from pathlib import Path

from . import knowledge as K
from . import text as T

URL = "https://photos.autorus.ru/upload/"
# Слова, которые не говорят, КАКАЯ это деталь: сторона, ось, комплектность, служебные
_SKIP = {T.stem(w) for w in ("передний", "задний", "левый", "правый", "верхний", "нижний", "комплект", "сборе", "подсборе",
                             "для", "штука", "пара", "оригинал", "аналог")}
MIN_SCORE = 0.6   # доля слов детали Кирилла, которые есть у позиции прайса


def build(xlsx: str, out: str):
    """Выжимка прайса: позиции с фото — группа, название, артикул, бренд, применяемость, ABC. Без цен и остатков."""
    import openpyxl   # только для выжимки: в службе не нужен
    rows = list(openpyxl.load_workbook(xlsx, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    head = next(i for i, r in enumerate(rows) if r and "Артикул" in [str(c or "").strip() for c in r])
    col = {str(c or "").strip(): i for i, c in enumerate(rows[head])}
    get = lambda r, name: str(r[col[name]] or "").strip() if col.get(name) is not None and col[name] < len(r) else ""
    parts = []
    for r in rows[head + 1:]:
        url = get(r, "Столбец1")
        if not url.startswith(URL):
            continue
        name, fits = get(r, "Наименование полное")[:200], get(r, "Применяемость")[:200]
        # Применяемость обычно повторяет хвост названия — храним, только если в ней есть что-то сверх (память службы)
        parts.append({"g3": get(r, "Группа уровень 3"), "name": name, "art": get(r, "Артикул"),
                      "fits": "" if fits.upper() in name.upper() else fits, "abc": get(r, "ABC Ранг"),
                      "photo": url[len(URL):]})
    Path(out).write_text(json.dumps({"_": "Выжимка прайса СТМ Авторусь без цен: python -m app.podbor.stm", "parts": parts},
                                    ensure_ascii=False), encoding="utf-8")
    return len(parts)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Z0-9]+", text.upper().replace("-", "")))


def model_tokens(name: str) -> set[str]:
    """Модель машины словами каталога: «RX330/350» → RX330, RX350; «CAMRY» → CAMRY. Голые числа и короткое — мимо."""
    up = name.upper().replace("-", "")
    out = {t for t in re.findall(r"[A-Z][A-Z0-9]+", up) if len(t) >= 3 or re.search(r"\d", t)}
    for pre, nums in re.findall(r"\b([A-Z]+)(\d+(?:/\d+)+)", up):
        out |= {pre + n for n in nums.split("/")}
    return out


def _fits(models: set[str], toks: set[str]) -> bool:
    """Модель в тексте прайса: «RX330» целиком или раздельно — «RX II 300/330» (буквы и число модели — отдельные слова)."""
    for m in models:
        if m in toks:
            return True
        k = re.fullmatch(r"([A-Z]+)(\d+)", m)
        if k and k.group(1) in toks and k.group(2) in toks:
            return True
    return False


def _side(name: str) -> T.Side:
    """Сторона позиции прайса: ось — словами, лево/право — «L», «R» («L=R» — одна на обе стороны)."""
    s = T.side(name)
    if re.search(r"\bL\s*=\s*R\b", name):
        return T.Side(s.axis, "")
    lr = "left" if re.search(r"\bL\b", name) else "right" if re.search(r"\bR\b", name) else s.lr
    return T.Side(s.axis, lr)


class Price:
    def __init__(self, file: Path | None):
        self.file = file
        self._by_head: dict[str, list[dict]] | None = None
        self._by_art: dict[str, dict] = {}

    def _load(self) -> dict[str, list[dict]]:
        """Позиции по главному слову названия («Втулка рычага» — втулка, не рычаг): искать среди своих, а не среди 20 тыс."""
        if self._by_head is None:
            try:
                parts = json.loads(self.file.read_text(encoding="utf-8"))["parts"] if self.file else []
            except (OSError, ValueError, KeyError):
                parts = []   # выжимки нет — Кирилл работает без фото
            self._by_head = {}
            for p in parts:
                head = T.head_text(T.expand(re.split(r"\s+для\s+", p["name"])[0]))
                if head:
                    self._by_head.setdefault(head, []).append(p)
                self._by_art[re.sub(r"[^0-9A-Z]", "", p["art"].upper())] = p
        return self._by_head

    def photo_for(self, need: str, part: str, brand: str, model: str, numbers=()) -> dict | None:
        """Фото к детали: {"url", "exact", "label"} или None. need — как деталь назвал расшифровщик, part — каталог."""
        by_head = self._load()
        if not by_head:
            return None
        for n in numbers:   # в предложениях есть эта самая позиция СТМ — фото самого товара
            hit = self._by_art.get(re.sub(r"[^0-9A-Z]", "", str(n).upper()))
            if hit:
                return {"url": URL + hit["photo"], "exact": True, "label": hit["name"]}
        want = T.side(f"{need} {part}")
        makes = {re.sub(r"[^A-Z]", "", brand.upper())} | set(K.families().get(K.family(brand), ("", ()))[1])
        models = model_tokens(model)
        typical = set(K.table("photo_typical")["groups"])
        best: tuple | None = None
        for name in (need, part):
            q = [s for s in T.stems(T.expand(name)) if s not in _SKIP]
            head = T.head_text(T.expand(name))
            if not q or not head:
                continue
            for key_head, group in by_head.items():
                if not T.same(head, key_head):
                    continue
                for p in group:
                    if "_st" not in p:   # основы считаем один раз на позицию, при первом спросе
                        p["_st"] = tuple(T.stems(T.expand(f"{p['name']} {p['g3']}")))
                    have = p["_st"]
                    score = sum(1 for s in q if any(T.same(s, h) for h in have)) / len(q)
                    side = _side(p["name"])
                    if score < MIN_SCORE or side.conflicts(T.Side(want.axis, "")):
                        continue
                    toks = _tokens(f"{p['name']} {p['fits']}")
                    make = bool(makes & toks)
                    exact = make and _fits(models, toks)
                    if not exact and p["g3"] not in typical:
                        continue
                    # та же машина, та же марка (пример для Lexus — от Toyota, а не от ВАЗ), та же сторона, похожесть, ходовая
                    rank = (exact, make, not side.conflicts(want), score, p["abc"] == "A", p["abc"] == "B")
                    if best is None or rank > best[0]:
                        best = (rank, p)
        if best is None:
            return None
        (exact, *_), p = best
        return {"url": URL + p["photo"], "exact": exact, "label": p["name"]}


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2]), "позиций с фото →", sys.argv[2])
