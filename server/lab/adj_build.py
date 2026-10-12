"""Постоянный список прилагательных для T.head → app/data/podbor_adj.json.

Раньше T.stem пополнял T.ADJ по ходу работы службы: «ременный» в любом дереве или реплике делал основу «ремен»
прилагательным навсегда, и в «ремень генератора» главным словом становился «генератор»; ответ на «Шторка безопасности»
зависел от того, писал ли кто-то «безопасный» после перезапуска (12.10, стенд «фразы × машины»: 7 из 4745 фраз
меняли итог от порядка). Теперь список один и тот же всегда.

Основа — прилагательное, если в корпусе она почти всегда стоит в форме прилагательного: «передн» — да, «ремен»
(«ремень» в тысячах названий, «ременный» — в единицах) — нет. Корпус: названия из прайсов поставщиков (имя, частота),
деревья быстрых групп парка, словари робота. Корпус в git не попадает — только итоговый список основ.

    cd server
    python lab/adj_build.py C:/Users/mrstr/emb-lab/data/names.tsv .podbor-cache/trees
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from app.podbor import text as T  # noqa: E402

OUT = Path("app/data/podbor_adj.json")
MIN_ADJ = 3        # столько раз основа должна встретиться прилагательным
SHARE = 0.8        # и такая доля всех её употреблений


def adjectival(w: str, s: str) -> bool:
    """То же правило, что было в T.stem: окончание прилагательного и основа на -н/-ск/-ов…, но не «-ение/-ание»."""
    return bool(T._ADJ_END.search(w) and T._ADJ_STEM.search(s) and len(s) >= 4 and not re.search(r"[еа]ние$", w))


def main(names: str, trees: str) -> None:
    adj, noun = collections.Counter(), collections.Counter()

    def add(text: str, n: int = 1) -> None:
        for w in T.words(T.expand(text)):
            s = T.stem(w)
            if adjectival(w, s):
                adj[s] += n
            elif T.norm(w) != s:
                # Слово без окончания («передн.», «задн», «салон») ничего не говорит: это и сокращения прайсов, и мужской род.
                # Существительное видно по окончанию: «ремень», «ремня», «салона», «пружины»
                noun[s] += n

    with open(names, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if parts and parts[0]:
                add(parts[0], int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1)

    def walk(node) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            add(str(node.get("name") or node.get("quickGroupName") or ""))
            add(str(node.get("synonyms") or ""))
            walk(node.get("children") or [])
    for p in Path(trees).glob("*.json"):
        walk(json.loads(p.read_text(encoding="utf-8")).get("tree"))

    data = Path("app/data")
    rules = json.loads((data / "podbor_rules.json").read_text(encoding="utf-8"))
    for syn in rules.get("synonyms", []):
        for w in syn.get("words", []):
            add(w, 5)
    typos = json.loads((data / "podbor_typos.json").read_text(encoding="utf-8")).get("words", {})
    for v in typos.values():
        add(v, 5)

    keep = sorted(s for s, n in adj.items() if n >= MIN_ADJ and n >= SHARE * (n + noun[s]))
    lost = sorted((s for s, n in adj.items() if n >= MIN_ADJ and s not in keep), key=lambda s: -adj[s])
    OUT.write_text(json.dumps({"why": "основы-прилагательные для T.head; собрано lab/adj_build.py — не править руками",
                               "adj": keep}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"прилагательных: {len(keep)}; отвергнуто (чаще существительное): {len(lost)}")
    print("отвергнуто, первые:", ", ".join(f"{s} {adj[s]}/{noun[s]}" for s in lost[:60]))
    for s in sys.argv[3:]:
        print(f"  {s}: прил {adj[s]}, сущ {noun[s]} → {'ДА' if s in keep else 'нет'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
