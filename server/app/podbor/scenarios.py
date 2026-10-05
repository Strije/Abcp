"""Сценарии подбора: заявки и разговоры с ожидаемым ответом — проверка покрытия после каждой правки правил.

    python -m app.podbor.scenarios                         # data/podbor_scenarios.json и scenarios_local.json
    python -m app.podbor.scenarios -k колодки              # только те, где в названии есть «колодки»
    python -m app.podbor.scenarios -v                      # с текстом ответа клиенту на каждом шаге

Движок — локальный (проверяем свежий код), данные — гостевые запросы рабочего сервера, как у cli.py.
Цены и наличие меняются каждый день, поэтому проверяем смысл ответа, а не цифры: статус, найденные детали,
сторону, вопрос клиенту, слова в черновике. Ожидания шага (все необязательные):
  status    — ok / answer / order / chat / no_quick_groups …
  positions — сколько позиций в ответе
  found     — слова, каждое должно быть в названии какого-нибудь найденного варианта («цеп», «колодк»)
  absent    — слова, которых не должно быть в названиях найденных вариантов
  question  — true: бот спросил клиента; false: не спрашивал
  axis      — front / rear: все найденные варианты этой стороны
  picks     — сколько позиций оформлено
  text      — куски, которые должны быть в ответе клиенту; no_text — которых быть не должно
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from .cli import warranty_brands
from .engine import Engine, draft
from .sources import Remote

FILE = Path(__file__).resolve().parent.parent / "data" / "podbor_scenarios.json"
# Сценарии на настоящих заявках клиентов (их VIN) — только локально, в git не кладём: репозиторий публичный
LOCAL = Path(__file__).resolve().parent / "scenarios_local.json"


def check(res: dict, exp: dict) -> list[str]:
    bad = []
    variants = [v for p in res.get("positions", []) for v in p.get("variants", [])]
    names = [v["name"].lower() for v in variants]
    text = res.get("text", "")
    low = text.lower()
    if "status" in exp and res["status"] != exp["status"]:
        bad.append(f"статус {res['status']}, ждали {exp['status']}")
    if "positions" in exp and len(res.get("positions", [])) != exp["positions"]:
        bad.append(f"позиций {len(res.get('positions', []))}, ждали {exp['positions']}")
    for w in exp.get("found", []):
        if not any(w.lower() in n for n in names):
            bad.append(f"нет детали «{w}» (нашли: {', '.join(dict.fromkeys(names)) or '—'})")
    for w in exp.get("absent", []):
        if any(w.lower() in n for n in names):
            bad.append(f"лишняя деталь «{w}»")
    if "question" in exp:
        asked = any(p.get("question") for p in res.get("positions", []))
        if asked != exp["question"]:
            bad.append("бот спросил клиента, а не должен" if asked else "бот не спросил клиента")
    if "axis" in exp and any(v["axis"] != exp["axis"] for v in variants):
        bad.append(f"не все варианты {exp['axis']}: " + ", ".join(v["axis"] or "?" for v in variants))
    if "picks" in exp:
        n = len((res.get("reply") or {}).get("picks", []))
        if n != exp["picks"]:
            bad.append(f"оформлено {n}, ждали {exp['picks']}")
    for w in exp.get("text", []):
        if w.lower() not in low:
            bad.append(f"в ответе нет «{w}»")
    for w in exp.get("no_text", []):
        if w.lower() in low:
            bad.append(f"в ответе лишнее «{w}»")
    return bad


async def run(scenarios: list[dict], engine: Engine, verbose: bool) -> tuple[int, int]:
    ok_steps = steps = 0
    failed = []
    for sc in scenarios:
        memory, errors = None, []
        for i, turn in enumerate(sc["turns"], 1):
            steps += 1
            try:
                res = await engine.run(turn["say"], turn.get("vehicle"), memory)
                res["text"] = draft(res)
                bad = check(res, turn.get("expect", {}))
            except Exception as e:   # сценарий не должен ронять весь прогон
                res, bad = {"text": ""}, [f"ошибка {type(e).__name__}: {e}"]
            memory = res.get("memory") or memory
            if bad:
                errors.append((i, turn["say"], bad, res.get("text", "")))
            else:
                ok_steps += 1
            if verbose:
                print(f"--- {sc['name']} / шаг {i}: {turn['say']}\n{res.get('text', '')}\n")
        mark = "✓" if not errors else "✗"
        print(f"{mark} {sc['name']}")
        for i, say, bad, text in errors:
            print(f"    шаг {i} «{say[:70]}»: " + "; ".join(bad))
        if errors:
            failed.append(sc["name"])
        sys.stdout.flush()
    print(f"\nСценариев: {len(scenarios) - len(failed)} из {len(scenarios)}, шагов: {ok_steps} из {steps}")
    return ok_steps, steps


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Проверка покрытия: сценарии заявок и разговоров")
    ap.add_argument("file", nargs="*", default=[str(FILE)] + ([str(LOCAL)] if LOCAL.exists() else []))
    ap.add_argument("-k", default="", help="только сценарии с этим словом в названии")
    ap.add_argument("-v", action="store_true", help="печатать ответ клиенту на каждом шаге")
    ap.add_argument("--remote", default="https://109.73.199.217")
    ap.add_argument("--cache", default=".podbor-cache")
    args = ap.parse_args(argv)
    scenarios = [s for f in args.file for s in json.loads(Path(f).read_text(encoding="utf-8"))
                 if args.k.lower() in s["name"].lower()]
    src = Remote(args.remote)
    try:
        ok, total = await run(scenarios, Engine(src, Path(args.cache), warranty_brands()), args.v)
    finally:
        await src.close()
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
