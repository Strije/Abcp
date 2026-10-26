"""Сводка кандидатов в словарь переводов каталога: что Кирилл выбрал под другим названием, чем назвал клиент.

    python -m app.assistant.candidates /var/lib/avtodrug-api/assistant/candidates.jsonl [дней]

Кирилл пишет кандидата, когда выбранная деталь в каталоге называется не так, как её назвал клиент («пыльник
амортизатора» → «Изолятор передней спиральной пружины, верхний»), — по схеме, по названиям продавцов или вторым
мнением. Повторы склеиваются по марке, началу номера и названию каталога. Принятое человеком — запись в
catalog_renames файла data/podbor_knowledge.json (и тест с настоящей фразой); сам Кирилл в словарь не пишет.
"""
import collections
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _base(part: str) -> str:
    """Название без стороны: «…, верхний правый» и «…, верхний левый» — одна запись."""
    return re.sub(r"[,\s]*(?:правый|левый|правая|левая|правое|левое|RH|LH)\b.*$", "", part, flags=re.I).strip(" ,")


def summary(path: Path, days: int = 7) -> list[dict]:
    edge = (datetime.now() - timedelta(days=days)).isoformat()
    groups: dict[tuple, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("t", "") < edge:
            continue
        key = (r.get("family") or r.get("brand", ""), re.sub(r"[^0-9A-Z]", "", str(r.get("oem", "")).upper())[:5],
               _base(r.get("part", "")))
        g = groups.setdefault(key, {"family": key[0], "oem": key[1], "part": key[2], "count": 0,
                                    "needs": collections.Counter(), "heard": collections.Counter(), "why": ""})
        g["count"] += 1
        g["needs"][r.get("need", "").lower()] += 1
        for names in (r.get("heard") or {}).values():
            if isinstance(names, list):
                g["heard"].update(names)
        g["why"] = g["why"] or r.get("why", "")
    return sorted(groups.values(), key=lambda g: -g["count"])


def show(path: str, days: int = 7):
    rows = summary(Path(path), int(days))
    print(f"Кандидатов за {days} дн.: {len(rows)}\n")
    for g in rows:
        print(f"• {g['family'] or '?'} {g['oem']}… «{g['part']}» — {g['count']} раз")
        print(f"  клиенты: {', '.join(n for n, _ in g['needs'].most_common(3))}")
        if g["heard"]:
            print(f"  продавцы: {', '.join(n for n, _ in g['heard'].most_common(4))}")
        print(f"  почему: {g['why'][:200]}\n")


if __name__ == "__main__":
    show(*sys.argv[1:])
