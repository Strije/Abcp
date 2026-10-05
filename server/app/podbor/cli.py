"""Прогон заявок через подбор и отчёт для проверки глазами.

    python -m app.podbor.cli заявки.txt --remote https://109.73.199.217 > отчёт.md

Заявки в файле разделяются строкой «---». С --remote данные берутся через гостевые запросы
рабочего сервера, пароли не нужны. Кэш дерева групп — в папке --cache (по умолчанию .podbor-cache).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from .engine import Engine
from .sources import Remote

WARRANTY = Path(__file__).resolve().parent.parent / "data" / "brand_warranty.json"


def warranty_brands() -> set[str]:
    try:
        return set(json.loads(WARRANTY.read_text(encoding="utf-8")).get("brands", {}))
    except (OSError, ValueError):
        return set()


def requests_from(path: str) -> list[str]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return [r.strip() for r in text.split("\n---") if r.strip()]


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Подбор по заявкам: машина, номер, сторона, аналоги")
    ap.add_argument("file", help="файл с заявками (разделитель — строка ---) или - для stdin")
    ap.add_argument("--remote", default="https://109.73.199.217", help="адрес рабочего сервера")
    ap.add_argument("--cache", default=".podbor-cache", help="папка кэша дерева групп")
    ap.add_argument("--json", action="store_true", help="полный ответ в JSON вместо отчёта")
    args = ap.parse_args(argv)

    src = Remote(args.remote)
    engine = Engine(src, Path(args.cache), warranty_brands())
    out = []
    try:
        for i, text in enumerate(requests_from(args.file), 1):
            res = await engine.run(text)
            if args.json:
                out.append(res)
                continue
            print(f"## Заявка {i} — {res['status']}, {res['seconds']} с\n")
            print("```\n" + text + "\n```\n")
            for p in res["positions"]:
                groups = ", ".join(f"{g['name']} ({g['score']})" for g in p["groups"]) or "—"
                print(f"- «{p['query']}»: {p['status']}; группы: {groups}")
                for v in p["variants"]:
                    side = "/".join(x for x in (v["axis"], v["lr"]) if x) or "сторона ?"
                    src_ = f" по: {v['side_source']}" if v["side_source"] else ""
                    print(f"  - {v['oem']} «{v['name']}» [{side}{src_}; поставщики перед {v['vote']['front']} / "
                          f"зад {v['vote']['rear']}] узел «{v['unit']}» {v['unit_note']}")
            print("\nЧерновик ответа:\n")
            print("\n".join("> " + line for line in res["text"].splitlines()) + "\n")
            sys.stdout.flush()
    finally:
        await src.close()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
