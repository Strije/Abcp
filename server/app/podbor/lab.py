"""Лаборатория массовых проверок: много реальных фраз × много машин → группы ошибок → правка → повтор и сравнение.

    python -m app.podbor.lab phrases chats.jsonl фразы.tsv [300]   # выбрать фразы клиентов по типам (жаргон, фирма, несколько…)
    python -m app.podbor.lab run фразы.tsv отчёт.json              # прогнать по деревьям парка (fleet_ready.json), без Laximo и цен
    python -m app.podbor.lab compare до.json после.json            # что стало лучше, а что хуже после правки
    python -m app.podbor.lab clean фразы.tsv чистые.tsv            # убрать из готового набора мусор (реклама, речь продавца)

Парк — деревья машин из .podbor-cache (python -m app.podbor.fleet pick|trees), реальные фразы — из выгрузки переписки
(bitrix_export.py, без персональных данных). Всё лежит в .podbor-cache, в git не попадает.

Проверки без «правильных ответов» — по тому, что видно по самим деревьям:
  потеряно слово    — группа выбрана уверенно, но слова клиента в её названии нет («прокладка крышки» → «головки»);
  везде мимо        — ни у одной машины парка группы нет (фраза не про деталь, или словаря не хватает);
  у части машин нет — группа есть у одних машин и нет у других (так бывает честно: «турбина» у бензинового атмо);
  несогласованно    — на разных машинах фраза ведёт в разные семейства групп (выбросы — кандидаты на ошибку);
  выбор везде       — бот спросит клиента на каждой машине (иногда верно: «свечи»; иногда нужна подсказка в правилах).
"""
import collections
import json
import random
import re
import sys
from pathlib import Path

from . import text as T
from .engine import share_noun

# Тип фразы — по признакам; у фразы может быть несколько
_NOISE = re.compile(r"₽|•|\bруб\b|нажм|кнопк|профил|устройств|сесси|контроль качества|агрегатор|подписк|канал\b|принять|отказ|"
                    r"спасибо|здравствуйте|добрый|привет|подъед|работаете|оплат|заберу|реквизит|скидк|безопасная сделка|"
                    r"отправ|номер заказа|карт[аеуы]\b|сбербанк|тинькофф|адрес|телефон", re.I)
_FIRM = re.compile(r"\b(?:bosch|бош|mann|манн|zekkert|зекерт|зеккерт|febest|ngk|ёнк|denso|gates|skf|sachs|trw|ate|lemforder|"
                   r"nissens|valeo|luk|ina|ruville|kayaba|monroe|bilstein|boge|hella|brembo|ferodo|textar|filtron|"
                   r"sangsin|mobis|мобис|фебест|кайаба)\b", re.I)
_SHORT_HAND = re.compile(r"[а-яa-z]/[а-яa-z]|\bк-т\b|\bрем\.?\s?комплект|\bс/б\b|\bш/о\b|\bг/ц\b|\bр/ц\b", re.I)
_SIDE = re.compile(r"перед|задн|лев|прав|верхн|нижн|внутренн|наружн|в круг", re.I)
_MANY = re.compile(r",|\+|\s и \s|\sа также\s", re.I)


# Марки машин и «в сборе» в тексте клиента — не детали: без них «лишних» слов не бывает
_MAKES = re.compile(r"\b(?:mitsubishi|митсубиси|митсубиши|opel|опель|bmw|бмв|ford|форд|nissan|ниссан|toyota|тойота|"
                    r"hyundai|хендай|хундай|kia|киа|кия|mazda|мазда|renault|рено|peugeot|пежо|citroen|ситроен|honda|хонда|"
                    r"skoda|шкода|audi|ауди|volkswagen|фольксваген|мерседес|mercedes|chevrolet|шевроле|lacetti|лачетти|"
                    r"солярис|рио|ceed|сид|инсигния|insignia|lanos|ланос)\b|в\s+сборе|\d{4}\s*г(?:ода|\.)?", re.I)


# Не просьба покупателя о детали — в лабораторию не берём: иначе робот «ищет» деталь в рекламе тарифа
# («🌐 15 ГБ интернет…» → «рабочий цилиндр»), а процент «уверенно» и правки подгоняются под шум.
# Реклама, рассылки, новости, предложения поставщиков
_ADS = re.compile(r"интернет|\bгб\b|кбит|безлимит|подписк|тариф|рынок\s+рф|регион\s+рф|канал\w*\s+в\s+т|телеграм|"
                  r"поставляем|посредник|низк\w+\s+цен|потребност\w+\s+в\s+закупк|актуальн\w+\s+прайс|прайс-?лист|"
                  r"сотрудничеств|оптов|дилерск|геран|бесплатно|пару\s+шагов|розыгрыш|подпиш|нового\s+образца|\bвпн\b|\bvpn\b", re.I)
# Речь продавца или менеджера, а не покупателя
_SELLER = re.compile(r"\b(?:мы|наш[аеиу]?|нам)\b[^.?!]{0,25}\b(?:можем|поставля|прода[её]м|привез[её]м|работаем|рабочий\s+день)|"
                     r"рабочий\s+день|руководител|часто\s+их\s+прода|в\s+сво[её]м\s+бюджете|годом\s+гарантии|"
                     r"не\s+ответили|наличие\s+уточн|уточним\s+и|подскаж\w+\s+пожалуйста\s+ваш", re.I)
# Не автомобиль
_NOT_CAR = re.compile(r"iphone|айфон|чехол\s+(?:для|на)\s+(?:тел|айф|iph)|телефон\w*\s+держател|магнитн\w+\s+держател", re.I)
# Где заказ, когда приедет — продолжение разговора; без названия детали в лабораторию не идёт
_STATUS = re.compile(r"пришл[оаи]|прид[её]т|подтвержден|забрать|получил|отправ|трек|доставк|\(изменено\)", re.I)
# Значки рекламы и рассылок: в просьбах клиентов их почти нет (🛞 и 🥰 из карточек Авито — бывают, их не трогаем)
_AD_EMOJI = re.compile("[\U0001F310\u267E\U0001F6E1\U0001F449\u2705\U0001F525\U0001F4A5\U0001F4E2\u26A1\U0001F381\U0001F4B0\u2B55]")


def junk(p: str, has_part) -> str:
    """Почему фраза — не просьба покупателя о детали («» — годится). has_part(p): есть ли в ней слово детали
    из словаря каталога (существительное, не «рабочий» и не «передний»)."""
    if re.fullmatch(r"\W*\(изменено\)\W*", p):
        return "служебное"
    if _ADS.search(p) or _AD_EMOJI.search(p):
        return "реклама/рассылка"
    if _SELLER.search(p):
        return "речь продавца"
    if _NOT_CAR.search(p):
        return "не для машины"
    if _STATUS.search(p) and not has_part(p):
        return "статус заказа"
    if re.search(r"\[(?:тел|почта|карта)\]|:\s*не установлено", p, re.I):
        return "контакты/анкета"
    if not has_part(p):
        return "нет детали"   # «Телевизор», «Я просто за рулём…»: ни одного слова каталога, даже с опечаткой
    return ""


def _has_part():
    """Есть ли в фразе существительное из словаря каталога — признак, что речь о детали."""
    from .measure import INDEX, STOP

    def has(p: str, loose: bool = False) -> bool:
        # Точно из словаря: known() правит опечатки и находит «деталь» и в «(изменено)».
        # loose — с опечатками и жаргоном («ступчитые»): хоть одно слово каталога должно быть
        # Без отсева «прилагательных»: T.ADJ считает ими и «ремен», «сцеплен» — «Ремень» выпадал бы как не деталь
        # Опечатку правим только в слово словаря: «масленный» → «масляный», «напужний» → «наружний» — деталь;
        # «кешбэк», «консультация» — нет (known() находил «деталь» и там)
        def word(s: str) -> bool:
            s = s.rstrip(".")   # «шрус.» в конце фразы — сокращение для T.stems
            return s in INDEX.vocab or (len(s) >= 5 and INDEX.fix(s) in INDEX.vocab)
        return any(len(s) > 2 and (INDEX.known([s]) if loose else word(s)) for s in T.stems(p, STOP))
    return has


def phrase_types(p: str, vocab_known) -> list[str]:
    """Тип фразы по признакам: нужен, чтобы группировать ошибки («жаргон» ошибается чаще «простых»)."""
    tags = []
    words = p.split()
    if _FIRM.search(p):
        tags.append("с фирмой")
    if _SHORT_HAND.search(p):
        tags.append("сокращения")
    if _MANY.search(p):
        tags.append("несколько деталей")
    if _SIDE.search(p):
        tags.append("сторона/узел")
    if len(words) > 6:
        tags.append("длинная")
    if vocab_known is not None and not vocab_known(p):
        tags.append("жаргон/опечатки")
    return tags or ["простая"]


def phrases(chats: str, out: str, n: str = "300") -> None:
    from .measure import INDEX, STOP, chat_pieces
    pieces, info = chat_pieces(chats)
    uniq = collections.Counter(p.strip() for p in pieces)
    part = _has_part()
    pool = [p for p in uniq if 6 <= len(p) <= 80 and not _NOISE.search(p) and not re.search(r"https?:|\d{9,}|@", p)
            and not junk(p, part)]
    # «всё слова в словаре каталога» — прокси для простых фраз; остальные — жаргон, опечатки, редкие детали
    known = lambda p: all(INDEX.known([s]) for s in T.stems(p, STOP) if len(s) > 3)  # noqa: E731
    by_type: dict[str, list[str]] = collections.defaultdict(list)
    for p in pool:
        for t in phrase_types(p, known):
            by_type[t].append(p)
    rnd = random.Random(7)
    want = int(n)
    chosen: dict[str, list[str]] = {}
    per = max(10, want // max(len(by_type), 1))
    for t, items in sorted(by_type.items()):
        # чаще встречающиеся фразы важнее: выборка с весом по частоте
        items = sorted(set(items), key=lambda p: -uniq[p])
        top = items[: per // 2] + rnd.sample(items[per // 2:], min(per - per // 2, max(len(items) - per // 2, 0)))
        for p in top:
            chosen.setdefault(p, []).append(t)
    rows = list(chosen.items())[:want * 2]
    Path(out).write_text("\n".join(f"{p}\t{','.join(ts)}\t{uniq[p]}" for p, ts in rows) + "\n", encoding="utf-8")
    print(f"Фраз в наборе: {len(rows)} из {len(pool)}; по типам: "
          + ", ".join(f"{t} {sum(1 for _, ts in rows if t in ts)}" for t in sorted(by_type)))


def _kind(rank: list, st: list[str], tree) -> tuple[str, str, float]:
    """Как фраза поняла эту машину: (вид, группа, оценка). Виды — как в measure.fleet."""
    from .measure import lost_words
    if not rank or rank[0][1] < 0.5:
        return "мимо", "", 0.0
    g, s = rank[0]
    if len(rank) > 1 and rank[1][1] >= s - 0.05 and rank[1][0].name.lower() != g.name.lower():
        return "выбор", f"{g.name} | {rank[1][0].name}", s
    kind = "уверенно" if s >= 0.8 else "слабо"
    if lost_words(st, tree, g) and kind == "уверенно":
        kind = "потеряно слово"
    return kind, g.name, s


def run(path: str, out: str, mode: str = "") -> None:
    """mode=nogate — без порога «пустышек»: то, как было до правки (для сравнения `compare`)."""
    from .engine import Engine, kind_of
    from .measure import ENGINE, STOP, _fleet_trees
    gate = mode == "nogate"
    trees = _fleet_trees()
    rows = [x.split("\t") for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    print(f"Машин в парке: {len(trees)}, фраз: {len(rows)}")
    report = []
    for row in rows:
        q, types = row[0], (row[1].split(",") if len(row) > 1 else [])
        clean = re.sub(r"\s+", " ", _MAKES.sub(" ", q)).strip()
        # «Диски и колодки передние» — две детали, как делит движок; худший результат из частей определяет фразу
        parts = [x for x in share_noun(T.split_pieces(clean), STOP) if T.stems(x, STOP)] or [clean]
        stems_of = lambda x: T.stems(T.expand(x), STOP)   # noqa: E731
        if all(ENGINE.outside(stems_of(x)) or kind_of(stems_of(x)) != "part" for x in parts):
            report.append({"query": q, "types": types, "category": "не каталог", "kinds": {}, "groups": []})
            continue
        if not gate and all(Engine._vague(stems_of(x), x) for x in parts if kind_of(stems_of(x)) == "part"):
            # Одно общее слово или один признак: бот спросит, а не угадает (порог «пустышек» в движке)
            report.append({"query": q, "types": types, "category": "уточняем", "kinds": {}, "groups": []})
            continue
        kinds, groups, per_car = collections.Counter(), collections.Counter(), {}
        for car, tree in trees.items():
            worst = ("уверенно", "", 1.0)
            for x in parts:
                st = T.stems(x, STOP)
                if ENGINE.outside(st):
                    continue
                got = _kind(tree.rank(st, T.side(x)), st, tree)
                if _BAD.get(got[0], 0) >= _BAD.get(worst[0], 0):
                    worst = got
                if got[1] and got[0] != "мимо":
                    groups[got[1].split(" | ")[0].lower()] += 1
            kinds[worst[0]] += 1
            per_car[car] = (worst[0], worst[1])
        n = max(sum(kinds.values()), 1)
        found = n - kinds["мимо"]
        top_share = (groups.most_common(1)[0][1] / max(found, 1)) if groups else 0
        if kinds["потеряно слово"]:
            cat = "потеряно слово"
        elif kinds["мимо"] >= 0.9 * n:
            cat = "везде мимо"
        elif kinds["мимо"] >= 0.3 * n:
            cat = "у части машин нет"
        elif len(groups) >= 3 and top_share < 0.6:
            cat = "несогласованно"
        elif kinds["выбор"] >= 0.6 * n:
            cat = "выбор везде"
        elif kinds["слабо"] >= 0.5 * n:
            cat = "слабо"
        else:
            cat = "уверенно"
        report.append({"query": q, "types": types, "category": cat, "kinds": dict(kinds), "groups": groups.most_common(4),
                       "lost_cars": [c for c, (k, _) in per_car.items() if k == "потеряно слово"][:3]})
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    _print(report)


_BAD = {"уверенно": 0, "слабо": 1, "выбор": 2, "мимо": 3, "потеряно слово": 4}
ORDER = ["потеряно слово", "несогласованно", "слабо", "выбор везде", "у части машин нет", "везде мимо", "уверенно", "уточняем", "не каталог"]


def _print(report: list[dict]) -> None:
    total = collections.Counter(r["category"] for r in report)
    print("\nИтого по фразам: " + ", ".join(f"{c} {total[c]}" for c in ORDER if total[c]))
    # Группы ошибок: категория × тип фразы
    grid = collections.defaultdict(collections.Counter)
    for r in report:
        for t in r["types"] or ["?"]:
            grid[t][r["category"]] += 1
    print("\nТип фразы → доля «уверенно» и проблемные категории:")
    for t, c in sorted(grid.items(), key=lambda x: -sum(x[1].values())):
        n = sum(c.values())
        bad = ", ".join(f"{k} {c[k]}" for k in ORDER[:6] if c[k])
        print(f"  {t:<18} {n:>4}  уверенно {100 * c['уверенно'] // n:>3}%   {bad}")
    for cat in ORDER[:6]:
        rows = [r for r in report if r["category"] == cat]
        if not rows:
            continue
        print(f"\n== {cat}: {len(rows)}")
        for r in rows[:14]:
            g = "; ".join(f"{n} ×{c}" for n, c in r["groups"][:2]) or "—"
            print(f"  {r['query'][:52]:<52} [{','.join(r['types'])[:22]}] → {g[:60]}")


def _dl(a: str, b: str) -> int:
    """Расстояние Дамерау — Левенштейна (замена, вставка, пропуск, перестановка соседних букв)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def typos(chats: str, min_count: str = "3", top: str = "150") -> None:
    """Кандидаты в словарь опечаток: частые слова переписки, которых нет в словаре каталога, но они на 1–2 буквы от слова
    каталога (одну букву `fix` правит сам). Проверяет человек: «готов» ≠ «голов» — частые обычные слова не опечатки."""
    from .chats import load
    from .measure import INDEX, STOP
    vocab = sorted(v for v in INDEX.vocab if len(v) >= 5 and not v.endswith("."))
    words, theirs = collections.Counter(), collections.Counter()
    for r in load(chats):
        for m in r["messages"]:
            for w in T.words(m["text"]):
                if len(w) >= 5 and re.fullmatch(r"[а-яё]+", w):
                    (words if m["role"] == "client" else theirs)[w] += 1
    # Слово, которое пишут и менеджеры, — обычное слово («заберу», «работа»), а не опечатка клиента
    for w in [w for w in words if theirs[w] >= 2]:
        del words[w]
    out = []
    for w, c in words.items():
        if c < int(min_count):
            continue
        st = T.stem(w)
        if INDEX.fix(st) != st or any(T.same(st, v) for v in INDEX.vocab):
            continue   # уже правится или это слово каталога
        lim = 1 if len(st) < 7 else 2
        best = min(((_dl(st, v), v) for v in vocab if abs(len(v) - len(st)) <= lim and v[:1] == st[:1]), default=None)
        if best and best[0] <= lim:
            out.append((c, w, st, best[1], best[0]))
    for c, w, st, v, d in sorted(out, reverse=True)[:int(top)]:
        print(f"{c:5} {w:<22} {st:<16} → {v:<16} (правок {d})")
    print(f"всего кандидатов: {len(out)}")


def compare(before: str, after: str) -> None:
    a = {r["query"]: r for r in json.loads(Path(before).read_text(encoding="utf-8"))}
    b = {r["query"]: r for r in json.loads(Path(after).read_text(encoding="utf-8"))}
    rank = {c: i for i, c in enumerate(ORDER)}
    better, worse = [], []
    for q in a.keys() & b.keys():
        ra, rb = a[q]["category"], b[q]["category"]
        if ra == rb:
            continue
        # «уверенно» — лучшее; «не каталог» и «везде мимо» не считаем ни улучшением, ни ухудшением
        score = lambda c: {"уверенно": 0, "уточняем": 0, "слабо": 1, "выбор везде": 1, "у части машин нет": 2,  # noqa: E731
                           "несогласованно": 3, "потеряно слово": 4}.get(c, 2)
        (better if score(rb) < score(ra) else worse if score(rb) > score(ra) else []).append((q, ra, rb))
    print(f"Улучшилось: {len(better)}, ухудшилось: {len(worse)}")
    for title, rows in (("Ухудшилось", worse), ("Улучшилось", better)):
        if rows:
            print(f"\n== {title}")
            for q, ra, rb in rows[:30]:
                print(f"  {q[:55]:<55} {ra} → {rb}")


def clean(path: str, out: str) -> None:
    """Убрать мусор из готового набора фраз (тот же фильтр, что при выборе) и показать, что убрано и почему."""
    part = _has_part()
    keep, gone = [], collections.defaultdict(list)
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        why = junk(line.split("\t")[0], part)
        (gone[why].append(line.split("\t")[0]) if why else keep.append(line))
    Path(out).write_text("\n".join(keep) + "\n", encoding="utf-8")
    print(f"Осталось фраз: {len(keep)}, убрано: {sum(len(v) for v in gone.values())}")
    for why, items in gone.items():
        print(f"\n== {why}: {len(items)}")
        for x in items:
            print("  " + x[:90])


if __name__ == "__main__":
    cmd = {"phrases": phrases, "run": run, "compare": compare, "typos": typos, "clean": clean}.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if not cmd:
        print(__doc__)
        raise SystemExit(1)
    cmd(*sys.argv[2:])
