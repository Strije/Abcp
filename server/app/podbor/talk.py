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
    args = ap.parse_args(argv)
    src = Remote(args.remote)
    engine = Engine(src, Path(args.cache), warranty_brands())
    mem = None
    try:
        for text in [t.strip() for t in args.turns.split("|") if t.strip()]:
            res = await engine.run(text, memory=mem)
            mem = res.get("memory") or mem
            print(f"\n=== КЛИЕНТ: {text}  [{res['status']}, {res.get('seconds')} с]")
            print(draft(res))
    finally:
        await src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
