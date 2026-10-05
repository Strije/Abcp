"""Переписка из открытых линий Битрикс24: что пишут покупатели и чем отвечают менеджеры.

    python -m app.podbor.chats stats chats.jsonl            # сколько с VIN, какие слова, где расходимся с Laximo
    python -m app.podbor.chats pairs chats.jsonl > pairs.jsonl   # «VIN + деталь → номер от менеджера»

Пары — эталон для замера точности: клиент прислал VIN и одну деталь, менеджер ответил номером.
Подбор считаем правильным, если номер менеджера есть среди наших оригиналов или аналогов.
Выгрузка — bitrix_export.py на сервере (портал пускает только его); данные в git не кладём.
"""
import collections
import json
import re
import sys
from pathlib import Path

from . import text as T

ARTICLE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9\-]*\d[A-Za-z0-9\-]*\d[A-Za-z0-9\-]*\d)[A-Za-z0-9][A-Za-z0-9\-]{4,19}(?![A-Za-z0-9])")
PRICE = re.compile(r"^\d{2,6}$")


def load(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if not r.get("skip"):
                rows.append(r)
    return rows


def _brand_price(a: str) -> bool:
    """«Zekkert1600», «LEMFORDER2300» — бренд и цена слитно (менеджер пишет «Zekkert 1600»), а не артикул."""
    m = re.fullmatch(r"([A-Za-z][A-Za-z\-]{2,})(\d{3,5})", a)
    if not m:
        return False
    from .. import brands as AB
    full = AB.get()
    return full.loaded and AB.norm(m.group(1)) in full.alias


def articles(text: str) -> list[str]:
    """Номера в ответе менеджера: буквы+цифры, не цена, не VIN и не «бренд + цена»."""
    out = []
    for m in ARTICLE.finditer(text):
        a = m.group(0).strip("-")
        if PRICE.match(a) or len(re.sub(r"\W", "", a)) == 17 or re.fullmatch(r"\d{1,2}-\d{1,2}", a):
            continue
        if re.fullmatch(r"(?:19|20)\d\d", a) or _brand_price(a) or re.fullmatch(r"\d{3,6}[PР]", a, re.I) \
                or re.fullmatch(r"(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])(?:19|20)\d\d", re.sub(r"\D", "", a)) \
                or re.fullmatch(r"\d+[XХ]\d+", a, re.I):   # даты «20.06.2013» и размеры «30x72» — не номера
            continue
        out.append(re.sub(r"[\s\-.]", "", a).upper())
    return list(dict.fromkeys(out))


def first_ask(d: dict) -> tuple[str, str]:
    """Первые сообщения клиента до ответа менеджера — там и VIN, и что нужно."""
    out = []
    for m in d["messages"]:
        if m["role"] != "client":
            break
        out.append(m["text"])
    text = "\n".join(out)
    return text, T.find_ident(text)


def stats(rows: list[dict]):
    lines = collections.Counter(r["line"] for r in rows)
    with_vin = [r for r in rows if first_ask(r)[1]]
    print(f"Диалогов с перепиской: {len(rows)}; с VIN или номером кузова в первом сообщении: {len(with_vin)} "
          f"({len(with_vin) * 100 // max(len(rows), 1)}%)")
    print("По линиям:", ", ".join(f"{k}: {v}" for k, v in lines.most_common()))
    answered = sum(1 for r in with_vin if any(articles(m["text"]) for m in r["messages"] if m["role"] == "manager"))
    print(f"Из них менеджер ответил номером: {answered}")


def pairs(rows: list[dict]):
    """Эталон: клиент прислал VIN (часто — не сразу, а после «пришлите VIN») и одну-две позиции,
    менеджер в ответ назвал номер(а). Запрос — всё, что клиент написал до первого номера от менеджера."""
    n = 0
    for r in rows:
        before, answer = [], []
        for m in r["messages"]:
            if m["role"] == "manager" and articles(m["text"]):
                answer = articles(m["text"])
                break
            if m["role"] == "client" and not m["text"].startswith("Системное сообщение"):
                before.append(m["text"])
        text = "\n".join(before)
        ident = T.find_ident(text)
        if not ident or not answer:
            continue
        req = T.parse(text)
        chunks = [c for c in req.chunks if len(c) > 3]
        if not chunks or len(chunks) > 2 or sum(len(T.split_pieces(c)) for c in chunks) > 3:
            continue
        answer = [a for a in answer if a != ident]
        if answer:
            print(json.dumps({"sid": r["sid"], "line": r["line"], "created": r["created"], "ident": ident,
                              "query": " | ".join(chunks)[:250], "answer": answer[:10]}, ensure_ascii=False))
            n += 1
    print(f"пар: {n}", file=sys.stderr)


def _k(x: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x).upper()).lstrip("0")


async def check(path: str, remote: str, limit: int):
    """Точность на эталоне: номер менеджера среди наших оригиналов (OEM) или показанных аналогов."""
    import asyncio
    import random

    from .engine import Engine
    from .gaps import CACHE
    from .sources import Remote
    from .cli import warranty_brands
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    random.Random(5).shuffle(rows)
    rows = rows[:limit]
    src = Remote(remote)
    eng = Engine(src, CACHE, warranty_brands())
    res = collections.Counter()
    sem = asyncio.Semaphore(3)

    async def one(p):
        async with sem:
            try:
                r = await eng.run(f"VIN: {p['ident']}\n{p['query']}")
                if r["status"] == "choose_vehicle":   # несколько машин по VIN — берём первую
                    r = await eng.run(f"VIN: {p['ident']}\n{p['query']}", 0)
            except Exception as e:
                return p, "ошибка", str(e)[:60]
        if r["status"] != "ok":
            return p, r["status"], ""
        want = {_k(a) for a in p["answer"]}
        oems = {_k(v["oem"]) for pos in r["positions"] for v in pos["variants"]}
        shown = {_k(a["number"]) for pos in r["positions"] for v in pos["variants"] if v["offers"]
                 for a in v["offers"]["analogs"]}
        crosses = {_k(n) for pos in r["positions"] for v in pos["variants"] if v["offers"]
                   for n in v["offers"].get("numbers", [])}

        def hit(have: set[str]) -> bool:
            # BMW: менеджер пишет «6867175», в каталоге «34216867175» — значимы последние 7 цифр;
            # VAG: «3C0927225C» и «3C0927225CREH» — тот же номер с кодом цвета
            return bool(want & have) or any(len(w) >= 6 and (h.endswith(w) or (len(w) >= 8 and h.startswith(w)))
                                            for w in want for h in have)

        if hit(oems):
            return p, "оригинал совпал", ""
        if hit(shown):
            return p, "аналог в показанных", ""
        if hit(crosses):
            return p, "аналог среди всех предложений", ""
        if not r["positions"] or all(not pos["groups"] for pos in r["positions"]):
            return p, "в тексте нет детали", ""   # «Подойдёт ли для Teana?» — деталь в объявлении Авито
        if not any(pos["variants"] for pos in r["positions"]):
            return p, "не нашли деталь", ""
        return p, "другой номер", ", ".join(sorted(oems))[:60]

    for p, verdict, extra in await asyncio.gather(*(one(p) for p in rows)):
        res[verdict] += 1
        if verdict in ("другой номер", "не нашли деталь"):
            print(f"- {verdict}: «{p['query'][:70]}» менеджер {p['answer'][:3]} | мы {extra}", file=sys.stderr)
    await src.close()
    n = sum(res.values())
    judged = ("оригинал совпал", "аналог в показанных", "аналог среди всех предложений", "другой номер",
              "не нашли деталь")
    real = sum(res[k] for k in judged)
    print(f"\nПар проверено: {n}; с деталью в тексте и найденной машиной: {real}")
    for k, v in res.most_common():
        print(f"  {k}: {v} ({v * 100 // max(n, 1)}% от всех)")
    good = res["оригинал совпал"] + res["аналог в показанных"] + res["аналог среди всех предложений"]
    print(f"Точность на парах с деталью: {good}/{real} = {good * 100 // max(real, 1)}%")


if __name__ == "__main__":
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "check":
        import asyncio
        asyncio.run(check(path, sys.argv[3] if len(sys.argv) > 3 else "https://109.73.199.217",
                          int(sys.argv[4]) if len(sys.argv) > 4 else 100))
    else:
        {"stats": stats, "pairs": pairs}[cmd](load(path))
