"""То же, что rules_check.sh, но на своём компьютере: main и ветка — одновременно, эталон и сценарии (общие + клиентские).
Быстрее сервера (одно ядро) в несколько раз; модель не нужна, к Laximo и ценам сценарии ходят через наш сервер.

    cd server && python lab/rules_check_local.py <ветка>        # итог — .podbor-cache/rules-<ветка>/REPORT.txt и на экран

Нужно в server/.podbor-cache: trees/ и fleet_ready.json (деревья парка), etalon/ (labels.tsv, label_sample.jsonl),
brand_aliases.json (справочник брендов ABCP, /var/lib/avtodrug-api на сервере) — забираются с сервера лаборатории;
клиентские сценарии — app/podbor/scenarios_local.json (не в git).
Копия main — git worktree рядом с репозиторием (../../abcp-main-wt), кэш в ней — ссылка на этот.
"""
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
REPO = SERVER.parent
CACHE = SERVER / ".podbor-cache"
EVAL = SERVER / "lab" / "gold_eval.py"
LOCAL = SERVER / "app" / "podbor" / "scenarios_local.json"


def git(*args, cwd=REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()   # Windows по умолчанию читает cp1251


def tree_for(ref: str, path: Path) -> Path:
    """Рабочая копия ref (worktree): создаём один раз, дальше — переключаем. Кэш — ссылкой на общий."""
    if not path.exists():
        git("worktree", "add", "--detach", str(path), ref)
    else:
        git("checkout", "-q", "--detach", ref, cwd=path)
    link = path / "server" / ".podbor-cache"
    if not link.exists():
        if os.name == "nt":   # ссылка на папку без прав администратора — junction
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(CACHE)], check=True, capture_output=True)
        else:
            link.symlink_to(CACHE, target_is_directory=True)
    return path / "server"


def start(side: str, code: Path, out: Path) -> list[subprocess.Popen]:
    # STATE_DIR — кэш лаборатории: там справочник брендов ABCP (brand_aliases.json с сервера; без него «свечи ngk» теряют
    # фирму и сценарий проваливается и на main, и на ветке)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", STATE_DIR=str(CACHE))
    env.pop("TOKEN_SECRET", None)   # ссылки на схемы — адресами Laximo, ничего не пишем
    files = [str(code / "app" / "data" / "podbor_scenarios.json")] + ([str(LOCAL)] if LOCAL.exists() else [])
    gold = subprocess.Popen([sys.executable, str(EVAL), "run", str(CACHE / "etalon"), str(out / f"gold_{side}.json")],
                            cwd=code, env=env, stdout=open(out / f"gold_{side}.txt", "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    scen = subprocess.Popen([sys.executable, "-m", "app.podbor.scenarios", *files], cwd=code, env=env,
                            stdout=open(out / f"scen_{side}.txt", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    return [gold, scen]


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    branch = sys.argv[1]
    git("fetch", "-q", "origin")
    out = CACHE / f"rules-{branch.replace('/', '-')}"
    out.mkdir(parents=True, exist_ok=True)
    sides = {"main": tree_for("origin/main", REPO.parent / "abcp-main-wt"),
             "branch": tree_for(f"origin/{branch}", REPO.parent / "abcp-branch-wt")}
    commits = {s: git("log", "--oneline", "-1", cwd=p.parent) for s, p in sides.items()}
    print(f"main:  {commits['main']}\nветка: {commits['branch']}\nидёт… (эталон и сценарии, main и ветка вместе)", flush=True)
    procs = [p for s, code in sides.items() for p in start(s, code, out)]
    for p in procs:
        p.wait()
    lines = [f"Ветка: {commits['branch']}", f"main:  {commits['main']}",
             "" if LOCAL.exists() else "ВНИМАНИЕ: нет scenarios_local.json — клиентские сценарии не проверялись", ""]
    d = subprocess.run([sys.executable, str(EVAL), "diff", str(out / "gold_main.json"), str(out / "gold_branch.json")],
                       capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    lines += [d.stdout.rstrip(), ""]
    scen = {s: (out / f"scen_{s}.txt").read_text(encoding="utf-8").splitlines() for s in sides}
    fails = {s: sorted(x for x in scen[s] if x.startswith("✗")) for s in sides}
    lines += [f"Сценарии, main:  {(scen['main'] or ['—'])[-1]}", f"Сценарии, ветка: {(scen['branch'] or ['—'])[-1]}"]
    new = [x for x in fails["branch"] if x not in fails["main"]]
    fixed = [x for x in fails["main"] if x not in fails["branch"]]
    if new:
        lines += ["", "Новые провалы сценариев (разобрать до выкладки):"]
        for x in new:
            i = scen["branch"].index(x)
            lines += [x] + [y for y in scen["branch"][i + 1:i + 7] if y.startswith("    шаг")]
    if fixed:
        lines += ["", "Починены сценарии:"] + fixed
    ok = d.returncode == 0 and not new
    lines += ["", "ИТОГ: по договору можно — эталон не хуже, новых провалов сценариев нет." if ok
              else "ИТОГ: РАЗОБРАТЬ — стало хуже (см. выше)."]
    text = "\n".join(lines)
    (out / "REPORT.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
