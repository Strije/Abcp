"""Разговор с роботом из консоли: свежий код, данные — гостевые запросы рабочего сервера (как cli.py).

    python -m app.podbor.talk "XW8ZZZ8K7AG200538 масло с фильтрами|первый вариант|да фильтра ещё"

Реплики — через «|», первая с VIN. Печатает ответ клиенту на каждом шаге — так проверяется то,
на чём споткнулся живой разговор, до выкладки. Модель (LLM) здесь не участвует: только правила.
"""
import argparse
import asyncio
from pathlib import Path

from .cli import warranty_brands
from .engine import Engine, draft
from .sources import Remote


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Разговор с роботом подбора: реплики через «|»")
    ap.add_argument("turns", help="реплики клиента через «|», первая — с VIN")
    ap.add_argument("--remote", default="https://109.73.199.217")
    ap.add_argument("--cache", default=".podbor-cache")
    ap.add_argument("--llm", action="store_true", help="подключить модель (LLM_URL, LLM_API_KEY в окружении)")
    ap.add_argument("--vehicle", type=int, default=None, help="какую из найденных по VIN машин брать (с 0)")
    args = ap.parse_args(argv)
    src = Remote(args.remote)
    llm = None
    if args.llm:
        from ..config import load
        from ..llm import LLM
        llm = LLM(load())
    engine = Engine(src, Path(args.cache), warranty_brands(), llm=llm)
    mem = None
    try:
        for text in [t.strip() for t in args.turns.split("|") if t.strip()]:
            res = await engine.run(text, vehicle=args.vehicle, memory=mem)
            mem = res.get("memory") or mem
            print(f"\n=== КЛИЕНТ: {text}  [{res['status']}, {res.get('seconds')} с]")
            print(draft(res))
    finally:
        await src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
