"""Замеры понимания запросов — без обращений к Laximo, по объединению сохранённых деревьев групп.

    python -m app.podbor.measure requests vinqu.jsonl            # заявки ABCP: одна группа / выбор / мимо
    python -m app.podbor.measure chats chats.jsonl [отчёт.json]   # переписка Битрикс24: как пишут клиенты
    python -m app.podbor.measure typos chats.jsonl                # какие «опечатки» правятся чаще всего
    python -m app.podbor.measure crosscheck vinqu.jsonl [chats.jsonl] [отчёт.json] [1500]  # разногласия каталогов
    python -m app.podbor.measure prices catalog.parquet [отчёт.json] [6000]  # названия одного артикула в разных прайсах
    python -m app.podbor.measure fleet запросы.txt [отчёт.json]  # запросы × деревья машин парка (fleet.py)
    python -m app.podbor.measure followups chats.jsonl [при.json]  # следующие сообщения: выбор, вопрос, уточнение…
    python -m app.podbor.measure snapshot vinqu.jsonl до.json      # разбор каждой позиции — для сравнения
    python -m app.podbor.measure diff до.json после.json           # что поменялось после правки

«Одна группа» — запрос однозначно ведёт в одну группу каталога (не значит «правильно», значит «без вопросов»),
«выбор» — несколько групп почти с одной оценкой (бот спросит), «мимо» — лучшая оценка ниже 0,5,
«не каталог» — химия, инструмент, шины. Переписку выгружает bitrix_export.py на сервере.
"""
import collections
import json
import re
import sys
from pathlib import Path

from . import text as T
from .catalog import TreeIndex
from .engine import Engine, load_rules, share_noun
from .engine import lost_words as _lost_words
from .gaps import positions
from .mine import union_tree

RULES = load_rules()
STOP = frozenset({w for w in RULES["stop"]} | {T.stem(w) for w in RULES["stop"]})
INDEX = TreeIndex(union_tree(), STOP, None, RULES["synonyms"],
                  frozenset(T.stem(w) for w in RULES.get("not_typos", {}).get("words", [])))


class _NoSource:
    laximo = None


ENGINE = Engine(_NoSource(), None, set(), RULES)
_vocab: dict[str, bool] = {}


def in_vocab(s: str) -> bool:
    if s not in _vocab:
        _vocab[s] = INDEX._in_vocab(s)
    return _vocab[s]


def classify(piece: str) -> tuple[str, list]:
    q = T.stems(piece, STOP)
    if ENGINE.outside(q):
        return "не каталог", []
    r = INDEX.rank(q, T.side(piece))
    if not r or r[0][1] < 0.5:
        return "мимо", r
    top = r[0][1]
    names = {g.name for g, s in r if s >= top - 0.05}
    return ("одна группа" if len(names) == 1 else "выбор из нескольких"), r


def _report(counts: collections.Counter, total: int) -> str:
    return ", ".join(f"{k} {v * 100 / max(total, 1):.1f}%" for k, v in counts.most_common())


def requests(path: str):
    asks = [p for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
            for p in positions(json.loads(line))]
    asks = [p for p in asks if T.stems(p, STOP)]
    c = collections.Counter(classify(p)[0] for p in asks)
    print(f"Позиций: {len(asks)} — {_report(c, len(asks))}")


def snapshot(path: str, out: str):
    asks = sorted({p for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
                   for p in positions(json.loads(line)) if T.stems(p, STOP)})
    data = {}
    for p in asks:
        k, r = classify(p)
        data[p] = [k, r[0][0].name if r else ""]
    Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def diff(a: str, b: str):
    A = json.loads(Path(a).read_text(encoding="utf-8"))
    B = json.loads(Path(b).read_text(encoding="utf-8"))
    for p in A:
        if A[p] != B.get(p):
            print(f"«{p[:60]}»: {A[p][0][:5]} {A[p][1]}  →  {B.get(p, ['—', ''])[0][:5]} {B.get(p, ['', ''])[1]}")


# ---------- переписка ----------

JUNK = re.compile(r"^(отправлено|открыть|системное|call miss|автодруг бот|\(изменено\))", re.I)


def chat_pieces(path: str) -> tuple[list[str], dict]:
    """Фразы клиентов про детали: первые 4 сообщения клиента, без шаблонов (текст в 15+ диалогах — автоматика)."""
    from .chats import load
    rows = load(path)
    freq = collections.Counter()
    for r in rows:
        for t in {m["text"].strip().lower()[:80] for m in r["messages"] if m["role"] == "client"}:
            freq[t] += 1
    tmpl = {t for t, c in freq.items() if c >= 15}
    pieces, with_part, memo = [], 0, {}
    for r in rows:
        got = False
        for m in [m for m in r["messages"] if m["role"] == "client"][:4]:
            t = m["text"]
            if t.strip().lower()[:80] in tmpl or JUNK.match(t.strip()):
                continue
            for ch in T.parse(t).chunks:
                for p in share_noun(T.split_pieces(ch) if "," in ch else [ch], STOP):
                    p = ENGINE.clean(p)
                    if not 3 <= len(p) <= 120:
                        continue
                    if p not in memo:
                        memo[p] = bool(INDEX.known(T.stems(p, STOP)))
                    if memo[p]:
                        pieces.append(p)
                        got = True
        with_part += got
    return pieces, {"dialogs": len(rows), "templates": len(tmpl), "with_part": with_part}


def chats(path: str, out: str | None = None):
    pieces, info = chat_pieces(path)
    uniq = collections.Counter(pieces)
    print(f"Диалогов: {info['dialogs']}, шаблонов: {info['templates']}, с деталью в начале: {info['with_part']}, "
          f"фраз про детали: {len(pieces)} (разных {len(uniq)})", flush=True)
    counts = collections.Counter()
    per = collections.defaultdict(list)
    for i, (p, c) in enumerate(uniq.items()):
        if i and i % 5000 == 0:
            print(f"  разобрано {i}/{len(uniq)}", flush=True)
        k, r = classify(p)
        counts[k] += c
        per[k].append((p, r))
    print(_report(counts, len(pieces)))
    if not out:
        return
    # Главные слова не из каталога и куда тянут соседние слова — кандидаты в словарь
    heads, ex, votes = collections.Counter(), collections.defaultdict(list), collections.defaultdict(collections.Counter)
    for p, c in uniq.items():
        st = [s for s in T.stems(p, STOP) if not s.endswith(".")]
        h = T.head(st) if st else None
        if not h or len(h) < 4 or in_vocab(INDEX.fix(h)):
            continue
        heads[h] += c
        if len(ex[h]) < 3:
            ex[h].append(p[:70])
        rest = [s for s in st if s != h]
        rr = INDEX.rank(rest, T.Side()) if rest else []
        if rr and rr[0][1] >= 0.6:
            votes[h][(rr[0][0].id, rr[0][0].name)] += 1
    report = {"counts": dict(counts), "info": info,
              "heads": [(h, c, votes[h].most_common(2), ex[h]) for h, c in heads.most_common(300)],
              "miss": [p for p, _ in per["мимо"]][:3000],
              "choose": [(p, [(g.name, round(s, 2)) for g, s in r[:3]]) for p, r in per["выбор из нескольких"]][:3000]}
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=0, default=str), encoding="utf-8")
    print(f"Отчёт: {out}")


# ---------- следующие сообщения: что клиент пишет после ответа с ценами ----------

PRICE_LINE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[  ]\d{3})+|\d{3,6})(?:[.,]\d{1,2})?\s*(?:р\b|р\.|руб|₽)", re.I)
HEADER = re.compile(r"^\s*\*?\s*\d{1,2}[.)]\s*(.+)$")


def _days(line: str) -> int:
    t = line.lower()
    if "на сегодня" in t or "в наличии" in t:
        return 0
    if "на завтра" in t or "завтра" in t:
        return 1
    m = re.search(r"(\d{1,2})(?:\s*-\s*\d{1,2})?\s*(?:дн|дня|дней|день)", t)
    return int(m.group(1)) if m else 2


def offer_memory(manager: str, asked: list[str]) -> dict:
    """Память, как будто ответ с ценами дал бот: позиции — заголовки «1. Колодки передние» или то,
    что спрашивал клиент; предложения — строки с ценой («ZEKKERT 1шт. 5850р. на сегодня»)."""
    from .dialog import brand_words
    positions: list[dict] = []

    def new_pos(query: str):
        positions.append({"query": query, "side": {}, "status": "found", "question": "", "turn": 1,
                          "variants": [{"name": query, "oem": "", "brand": "", "axis": T.side(query).axis,
                                        "lr": T.side(query).lr, "amount": "", "alt": False,
                                        "offers": {"original": None, "analogs": [], "stats": {}}}]})

    for line in manager.splitlines():
        line = line.strip(" *\t")
        if not line:
            continue
        price = PRICE_LINE.search(line)
        head = HEADER.match(line)
        if head and not price:
            new_pos(head.group(1).strip(" *"))
            continue
        if not price:
            continue
        if not positions:
            new_pos(asked[0] if asked else "деталь")
        brands = brand_words(line)
        o = {"brand": brands[0][0] if brands else line.split()[0].strip("-:"), "number": "", "description": line,
             "price": float(re.sub(r"\D", "", price.group(1))), "days": _days(line), "tags": [], "cheaper": None}
        offers = positions[-1]["variants"][0]["offers"]
        if re.search(r"\bориг", line, re.I) and not offers["original"]:
            offers["original"] = o
        else:
            offers["analogs"].append(o)
    return {"ident": "X", "plate": "", "turn": 1, "positions": positions}


def clean_reply(text: str) -> str:
    """Реплика клиента без того, что приклеил Битрикс: сообщения робота («Благодарим за заказ…»),
    цитаты между «------», «Открыть», «(изменено)», пропущенные звонки."""
    text = re.split(r"Отправлено роботом", text)[0]
    text = re.sub(r"-{10,}.*?(?:-{10,}|$)", " ", text, flags=re.S)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not re.fullmatch(r"(?:открыть|\(изменено\)|\[call\s*-\s*miss\]|\[тел\]|\[имя\].*)",
                                                            ln, re.I)]
    return "\n".join(lines).strip()


def followups(path: str, out: str | None = None):
    """Вторые сообщения клиентов: первое сообщение клиента после ответа менеджера с ценами,
    разобранное тем же dialog.plan, что работает на сервере. Дерево групп — объединение сохранённых."""
    import random
    from .chats import load
    from .dialog import KINDS, plan
    rows = load(path)
    freq = collections.Counter(m["text"].strip().lower()[:80] for r in rows for m in r["messages"] if m["role"] == "client")
    tmpl = {t for t, c in freq.items() if c >= 15}
    cases = []
    for r in rows:
        ms = r["messages"]
        i = next((i for i, m in enumerate(ms) if m["role"] == "manager" and PRICE_LINE.search(m["text"])), None)
        if i is None:
            continue
        nxt = next((t for m in ms[i + 1:] if m["role"] == "client"
                    and not JUNK.match(m["text"].strip()) and m["text"].strip().lower()[:80] not in tmpl
                    and re.search(r"[а-яёa-z]", t := clean_reply(m["text"]), re.I)), None)
        if not nxt or len(nxt) > 300:
            continue
        asked = [ENGINE.clean(p) for m in ms[:i] if m["role"] == "client"
                 for ch in T.parse(m["text"]).chunks for p in [ch] if INDEX.known(T.stems(p, STOP))]
        cases.append((nxt, offer_memory(ms[i]["text"], asked)))
    counts, handoff = collections.Counter(), collections.Counter()
    per = collections.defaultdict(list)
    for text, mem in cases:
        pl = plan(text, mem, STOP, INDEX, 3, ENGINE.clean) if mem["positions"] else {"kind": "chat"}
        kind = pl["kind"]
        counts[kind] += 1
        handoff[kind] += kind != "chat" and bool(pl.get("handoff"))
        per[kind].append(text)
    print(f"Диалогов с ответом-ценой и ответом клиента: {len(cases)}")
    for k, c in counts.most_common():
        part = f"  (из них {handoff[k]} — и вопрос менеджеру)" if handoff[k] else ""
        print(f"  {KINDS[k]:<38} {c:5}  {c * 100 / len(cases):5.1f}%{part}")
    if out:
        random.seed(int(__import__("os").environ.get("SEED", "7")))
        Path(out).write_text(json.dumps({k: random.sample(v, min(120, len(v))) for k, v in per.items()},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Примеры по видам: {out}")


# ---------- перекрёстная проверка по каталогам: без Laximo и поставщиков ----------

def _trees() -> dict[str, TreeIndex]:
    """Все сохранённые деревья групп (.podbor-cache/trees) — по одному на каталог."""
    nt = frozenset(T.stem(w) for w in RULES.get("not_typos", {}).get("words", []))
    out = {}
    for f in sorted(Path(".podbor-cache/trees").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("tree"):
            out[f.stem] = TreeIndex(d["tree"], STOP, None, RULES["synonyms"], nt)
    return out


def crosscheck(vinqu: str, chats_path: str | None = None, out: str | None = None, top: str = "1500"):
    """Частые запросы клиентов × все сохранённые каталоги. Номера быстрых групп Laximo общие для всех каталогов,
    поэтому разногласие — признак ошибки без всякой разметки: «комплект ГРМ» у большинства машин ведёт
    в «Комплект ремня ГРМ», а у VW — в «ШРУС», хотя группа ремня ГРМ у VW есть.
    Отчёт: выбросы (у каталога есть группа большинства, а выбран другой), промахи (у большинства нашлось,
    тут нет), неоднозначные запросы (две группы почти с одной оценкой у многих каталогов)."""
    freq = collections.Counter()
    for line in Path(vinqu).read_text(encoding="utf-8").splitlines():
        if line.strip():
            freq.update(ENGINE.clean(p).lower() for p in positions(json.loads(line)) if T.stems(p, STOP))
    if chats_path:
        pieces, _ = chat_pieces(chats_path)
        freq.update(p.lower() for p in pieces)
    queries = [q for q, _ in freq.most_common(int(top)) if 3 <= len(q) <= 80]
    trees = _trees()
    print(f"Запросов: {len(queries)}, каталогов: {len(trees)} ({', '.join(trees)})", flush=True)
    outliers, misses, unsure = [], [], collections.Counter()
    for n, q in enumerate(queries):
        if n and n % 200 == 0:
            print(f"  {n}/{len(queries)}", flush=True)
        st = T.stems(q, STOP)
        if ENGINE.outside(st):
            continue
        side = T.side(q)
        pick: dict[str, tuple] = {}
        for cat, tree in trees.items():
            r = tree.rank(st, side)
            if not r or r[0][1] < 0.5:
                pick[cat] = None
                continue
            tie = len(r) > 1 and r[1][1] >= r[0][1] - 0.05 and r[1][0].name.lower() != r[0][0].name.lower()
            # Ничья («фильтр» одним словом): бот спросит клиента, а не выберет — это не ошибка выбора
            pick[cat] = (r[0][0].id, r[0][0].name.lower()) if not tie else ("ничья", "ничья")
            unsure[q] += tie
        found = [p for p in pick.values() if p and p[0] != "ничья"]
        if len(found) < 3:
            continue
        ids = collections.Counter(p[0] for p in found)
        names = collections.Counter(p[1] for p in found)
        main_id, main_n = ids.most_common(1)[0]
        main_name = names.most_common(1)[0][0]
        if main_n < len(found) * 0.5:
            continue   # большинства нет — это не «выброс», а разные устройства у разных машин
        for cat, p in pick.items():
            if p and p[0] == "ничья":
                continue
            if p is None and len(found) >= len(trees) * 0.6 and main_id in trees[cat].groups:
                misses.append({"query": q, "catalog": cat, "majority": main_name, "freq": freq[q]})
            elif p and p[0] != main_id and p[1] != main_name and main_id in trees[cat].groups:
                # Честное разногласие — только при одинаковом выборе: у большинства тоже есть группа,
                # которую выбрал этот каталог. Иначе это «у них нет отдельной группы» («сальник распредвала»
                # у Ford — своя группа, у остальных — общая «Сальники»), а не ошибка.
                with_both = [c for c, x in pick.items() if x and x[0] == main_id and p[0] in trees[c].groups]
                if len(with_both) >= 2:
                    outliers.append({"query": q, "catalog": cat, "chosen": p[1], "majority": main_name,
                                     "agree": f"{len(with_both)} с обеими группами", "freq": freq[q]})
    outliers.sort(key=lambda x: -x["freq"])
    misses.sort(key=lambda x: -x["freq"])
    print(f"Выбросов: {len(outliers)}, промахов: {len(misses)}, запросов с выбором из двух почти равных групп: {len(unsure)}")
    for o in outliers[:40]:
        print(f"  «{o['query']}» ×{o['freq']}  {o['catalog']}: «{o['chosen']}», а у большинства ({o['agree']}) «{o['majority']}»")
    if out:
        Path(out).write_text(json.dumps({"outliers": outliers, "misses": misses,
                                         "unsure": unsure.most_common(300)}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"Отчёт: {out}")


# ---------- прайсы: одно и то же изделие под разными названиями ----------

def prices(path: str, out: str | None = None, sample: str = "6000"):
    """Названия одного артикула (бренд + номер) в разных прайсах — это одна деталь, значит и группа каталога
    у всех названий должна быть одна. Где робот разводит их по разным группам или не понимает часть
    названий — это формулировки, которых он не знает. Без Laximo и без поставщиков: только объединённое
    дерево групп. Дубли («Колодки торм. перед.» в десяти прайсах) считаются один раз."""
    import random
    import pyarrow.parquet as pq
    from ..abcp import _key
    names: dict[tuple, set[str]] = collections.defaultdict(set)
    pf = pq.ParquetFile(path)
    for i in range(pf.num_row_groups):
        for r in pf.read_row_group(i, columns=["name", "brand", "article"]).to_pylist():
            n = re.sub(r"\s+", " ", str(r["name"] or "")).strip().lower()
            if n and r["article"]:
                names[(_key(r["brand"]), _key(r["article"]))].add(n)
    multi = [(k, v) for k, v in names.items() if len(v) >= 3]
    print(f"Артикулов: {len(names)}, с 3+ разными названиями: {len(multi)}", flush=True)
    random.seed(5)
    multi = random.sample(multi, min(int(sample), len(multi)))
    memo: dict[str, tuple] = {}

    def top(n: str):
        if n not in memo:
            st = T.stems(n, STOP)
            if ENGINE.outside(st):
                memo[n] = ("не каталог",)
            else:
                r = INDEX.rank(st, T.side(n))
                memo[n] = (r[0][0].name.lower(),) if r and r[0][1] >= 0.5 else ("мимо",)
        return memo[n][0]

    agree = split = 0
    wrong, missed = collections.Counter(), collections.Counter()
    examples: dict[tuple, list] = collections.defaultdict(list)
    for j, (k, ns) in enumerate(multi):
        if j and j % 1000 == 0:
            print(f"  {j}/{len(multi)}", flush=True)
        got = {n: top(n) for n in ns}
        votes = collections.Counter(g for g in got.values() if g not in ("мимо", "не каталог"))
        if not votes:
            continue
        main, cnt = votes.most_common(1)[0]
        if cnt < len(ns) / 2:
            split += 1   # большинства нет — изделие называют совсем по-разному
            continue
        agree += 1
        for n, g in got.items():
            if g == "мимо":
                missed[main] += 1
                if len(examples[("мимо", main)]) < 6:
                    examples[("мимо", main)].append(n)
            elif g != main and g != "не каталог":
                wrong[(main, g)] += 1
                if len(examples[(main, g)]) < 6:
                    examples[(main, g)].append(n)
    total = sum(len(ns) for _, ns in multi)
    print(f"Изделий с большинством: {agree}, без большинства: {split}. Названий всего: {total}, "
          f"из них не в ту группу: {sum(wrong.values())}, не понял: {sum(missed.values())}")
    print("Чаще всего путает (группа большинства → куда ушло название):")
    for (a, b), c in wrong.most_common(30):
        print(f"  {c:4}  «{a}» → «{b}»: {'; '.join(examples[(a, b)][:3])}")
    print("Чаще всего не понимает (группа большинства: примеры названий):")
    for a, c in missed.most_common(20):
        print(f"  {c:4}  «{a}»: {'; '.join(examples[('мимо', a)][:3])}")
    if out:
        Path(out).write_text(json.dumps({"wrong": [[a, b, c, examples[(a, b)]] for (a, b), c in wrong.most_common(500)],
                                         "missed": [[a, c, examples[("мимо", a)]] for a, c in missed.most_common(300)]},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Отчёт: {out}")


# ---------- парк машин: запросы × деревья конкретных машин (fleet.py) ----------

def _fleet_trees() -> dict[str, TreeIndex]:
    nt = frozenset(T.stem(w) for w in RULES.get("not_typos", {}).get("words", []))
    ready = json.loads(Path(".podbor-cache/fleet_ready.json").read_text(encoding="utf-8"))
    out = {}
    for f in ready:
        p = Path(".podbor-cache/trees") / f"{f['tree']}.json"
        if p.exists():
            out[f"{f['make']} {f['name'][:40]}"] = TreeIndex(json.loads(p.read_text(encoding="utf-8"))["tree"],
                                                             STOP, None, RULES["synonyms"], nt)
    return out


def lost_words(q: list[str], tree: TreeIndex, g) -> list[str]:
    return _lost_words(q, tree, g, STOP)


def fleet(path: str, out: str | None = None):
    """Запросы из файла (по одному в строке) по деревьям всех машин парка: как понят — уверенно,
    слабо, выбор, мимо, не каталог, у машины нет; и «уверенно, но слово потеряно» — самое опасное."""
    trees = _fleet_trees()
    qs = [q.strip() for q in Path(path).read_text(encoding="utf-8").splitlines() if q.strip()]
    print(f"Машин: {len(trees)}, запросов: {len(qs)}")
    total, report = collections.Counter(), []
    for q in qs:
        st = T.stems(q, STOP)
        if ENGINE.outside(st):
            total["не каталог"] += 1
            report.append({"query": q, "main": "не каталог"})
            continue
        kinds, names, lost = collections.Counter(), collections.Counter(), collections.Counter()
        for car, tree in trees.items():
            r = tree.rank(st, T.side(q))
            if not r or r[0][1] < 0.5:
                kinds["мимо / у машины нет"] += 1
                continue
            g, s = r[0]
            if len(r) > 1 and r[1][1] >= s - 0.05 and r[1][0].name.lower() != g.name.lower():
                kinds["выбор"] += 1
                names[f"{g.name} | {r[1][0].name}"] += 1
                continue
            lw = lost_words(st, tree, g)
            kind = "уверенно" if s >= 0.8 else "слабо"
            if lw and kind == "уверенно":
                kind = "уверенно, но слово потеряно"
                lost[", ".join(lw)] += 1
            kinds[kind] += 1
            names[g.name] += 1
        main = kinds.most_common(1)[0][0] if kinds else "мимо / у машины нет"
        total[main] += 1
        report.append({"query": q, "main": main, "kinds": dict(kinds), "groups": names.most_common(3),
                       "lost": lost.most_common(2)})
    order = ["уверенно, но слово потеряно", "слабо", "выбор", "мимо / у машины нет", "уверенно", "не каталог"]
    for k in order:
        rows = [r for r in report if r["main"] == k]
        if not rows:
            continue
        print(f"\n== {k}: {len(rows)}")
        for r in rows:
            extra = f"  потеряно: {r['lost'][0][0]}" if r.get("lost") else ""
            print(f"  {r['query'][:50]:<50} → {'; '.join(f'{n} ×{c}' for n, c in r.get('groups', [])[:2])}{extra}")
    print("\nИтого: " + ", ".join(f"{k} {total[k]}" for k in order if total[k]))
    if out:
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- модель против правил: разбор сумбурных сообщений ----------

def llm_compare(chats_path: str, vinqu: str, out: str, n: str = "120", url: str = "https://109.73.199.217"):
    """Сообщения, которые бот отдал бы модели (несколько строк, «+», «:», «либо», длинные), из заявок и
    переписки. Каждое разбирают правила и модель (через сервер — ключ только там); позиции обоих оцениваем
    одинаково — уверенно ли ведут в одну группу каталога. Пароль страницы — в PODBOR_PASSWORD."""
    import os
    import random
    import time as _time
    import httpx
    from .chats import load
    from .engine import needs_llm
    msgs = []
    for line in Path(vinqu).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            body = str(r.get("claim") or r.get("query") or "")
            if needs_llm(T.parse(body).chunks):
                msgs.append(("заявка", body))
    for d in load(chats_path):
        first = next((m["text"] for m in d["messages"] if m["role"] == "client"), "")
        ch = T.parse(first).chunks
        if needs_llm(ch) and INDEX.known(T.stems(first, STOP)) and len(first) < 1500:
            msgs.append(("чат", first))
    random.seed(11)
    msgs = random.sample(msgs, min(int(n), len(msgs)))
    c = httpx.Client(base_url=url, auth=("m", os.environ["PODBOR_PASSWORD"]), timeout=120)
    rows, tot = [], collections.Counter()
    for i, (src, body) in enumerate(msgs, 1):
        req = T.parse(body)
        rules = [q for q, _ in ENGINE.split(req.chunks, INDEX)]
        t0 = _time.time()
        try:
            parsed = c.post("/v1/podbor/understand", json={"text": "\n".join(req.chunks)}).json().get("parsed")
        except Exception as e:   # сеть — отметим и пойдём дальше
            parsed = None
            print(f"  {i}: ошибка {e}")
        took = _time.time() - t0
        llm = [p["part"] for p in (parsed or {}).get("positions", [])]
        def score(qs):
            k = collections.Counter(classify(q)[0] for q in qs)
            return k
        sr, sl = score(rules), score(llm)
        tot["сообщений"] += 1
        tot["правила: позиций"] += len(rules)
        tot["правила: одна группа"] += sr["одна группа"]
        tot["модель: позиций"] += len(llm)
        tot["модель: одна группа"] += sl["одна группа"]
        tot["модель: не ответила"] += parsed is None
        tot["модель: секунд"] += took
        rows.append({"src": src, "text": body[:600], "rules": rules, "llm": (parsed or {}).get("positions"),
                     "questions": (parsed or {}).get("questions"), "seconds": round(took, 1)})
        if i % 10 == 0:
            print(f"  {i}/{len(msgs)}", flush=True)
    print(f"Сообщений: {tot['сообщений']}, модель не ответила: {tot['модель: не ответила']}, "
          f"среднее время модели: {tot['модель: секунд'] / max(tot['сообщений'], 1):.1f} с")
    for who in ("правила", "модель"):
        a, b = tot[f"{who}: позиций"], tot[f"{who}: одна группа"]
        print(f"  {who}: позиций {a} ({a / max(tot['сообщений'], 1):.1f} на сообщение), уверенно в одну группу "
              f"{b} ({b * 100 / max(a, 1):.0f}%)")
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Разборы рядом: {out}")


def followups_llm(path: str, out: str, n: str = "150", url: str = "https://109.73.199.217"):
    """Вторые сообщения: где правила не уверены, спрашиваем модель (через сервер) — что она поняла.
    Отчёт: доля неуверенных у правил, что сказала модель, и все пары «правила / модель» для просмотра."""
    import os
    import random
    import httpx
    from .chats import load
    from .dialog import plan
    rows = load(path)
    freq = collections.Counter(m["text"].strip().lower()[:80] for r in rows for m in r["messages"] if m["role"] == "client")
    tmpl = {t for t, c in freq.items() if c >= 15}
    cases = []
    for r in rows:
        ms = r["messages"]
        i = next((i for i, m in enumerate(ms) if m["role"] == "manager" and PRICE_LINE.search(m["text"])), None)
        if i is None:
            continue
        nxt = next((t for m in ms[i + 1:] if m["role"] == "client" and not JUNK.match(m["text"].strip())
                    and m["text"].strip().lower()[:80] not in tmpl
                    and re.search(r"[а-яёa-z]", t := clean_reply(m["text"]), re.I)), None)
        if not nxt or len(nxt) > 300:
            continue
        asked = [ENGINE.clean(p) for m in ms[:i] if m["role"] == "client"
                 for ch in T.parse(m["text"]).chunks for p in [ch] if INDEX.known(T.stems(p, STOP))]
        mem = offer_memory(ms[i]["text"], asked)
        if mem["positions"]:
            cases.append((nxt, mem))
    random.seed(21)
    cases = random.sample(cases, min(int(n), len(cases)))
    c = httpx.Client(base_url=url, auth=("m", os.environ["PODBOR_PASSWORD"]), timeout=120)
    report, tot = [], collections.Counter()
    for j, (text, mem) in enumerate(cases, 1):
        pl = plan(text, mem, STOP, INDEX, 3, ENGINE.clean)
        tot["всего"] += 1
        tot["правила уверены"] += pl["sure"]
        llm = None
        if not pl["sure"]:
            try:
                llm = c.post("/v1/podbor/understand", json={"text": text, "memory": mem}).json().get("parsed")
            except Exception as e:
                print(f"  {j}: ошибка {e}")
            if llm:
                from .dialog import from_llm
                fl = from_llm(llm, mem, 3, text)
                llm["final_picks"] = len((fl.get("reply") or {}).get("picks", []))
                llm["final_unclear"] = (fl.get("reply") or {}).get("unclear", [])
                k = ("уточнить" if llm["unsure"] or (llm["clarify"] and not llm["picks"]) or llm["final_unclear"] else
                     "выбор" if llm["picks"] else "вопрос" if llm["asks"] else
                     "новая/уточнение" if llm["new_parts"] or llm["refine"] else
                     "менеджеру" if llm["manager"] else "пусто")
                tot[f"модель: {k}"] += 1
        report.append({"text": text, "rules": pl["kind"], "sure": pl["sure"], "llm": llm,
                       "shown": [p["query"] for p in mem["positions"]]})
        if j % 25 == 0:
            print(f"  {j}/{len(cases)}", flush=True)
    for k, v in sorted(tot.items()):
        print(f"  {k}: {v}")
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Пары: {out}")


def typos(path: str):
    """Какие слова переписки «исправляются» как опечатки: частые — почти наверняка обычные слова (в not_typos)."""
    from .chats import load
    words = collections.Counter(s for r in load(path) for m in r["messages"] if m["role"] == "client"
                                for s in T.stems(m["text"], STOP))
    fixes = collections.Counter({(s, INDEX.fix(s)): c for s, c in words.items()
                                 if len(s) >= 5 and not s.endswith(".") and INDEX.fix(s) != s})
    for (s, f), c in fixes.most_common(80):
        print(f"{c:6} {s} → {f}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"requests": requests, "chats": chats, "typos": typos, "snapshot": snapshot, "diff": diff,
     "followups": followups, "crosscheck": crosscheck, "prices": prices, "fleet": fleet,
     "llm": llm_compare, "followups_llm": followups_llm}[cmd](*args)
