#!/bin/bash
# Проверка ветки по договору «когда правка считается улучшением» (docs/ROADMAP.md, раздел «Робот подбора»): эталон
# 550 фраз и все сценарии — общие и клиентские — на main и на ветке; на выходе разница списков провалов, а не только итог.
#
#   bash /srv/podbor-lab/repo/server/lab/rules_check.sh <ветка>   — обновит копии и запустит в фоне (служба podbor-rules-check)
#   cat /srv/podbor-lab/runs/rules-<ветка>/REPORT.txt              — итог, когда появится файл DONE (около 20 минут)
#
# Сервер лаборатории: /srv/podbor-lab/repo — ветка, /srv/podbor-lab/main-wt — main (git worktree, кэш общий).
# Без модели (сценарии и эталон — правила); замер на 100/300 переписках с моделью — отдельно, ночью.
set -u
B=${1:?укажите ветку}
LAB=/srv/podbor-lab
REF=$LAB/runs/2026-10-08-emb               # эталон: label_sample.jsonl + labels.tsv
LOCAL=$LAB/data/scenarios_local.json       # клиентские сценарии: VIN клиентов, в git не кладём
OUT=$LAB/runs/rules-${B//\//-}
PY=/opt/avtodrug-api/venv/bin/python

if [ "${2:-}" != "--inside" ]; then
  cd $LAB/repo && git fetch -q origin && git checkout -q -B "$B" "origin/$B" || exit 1
  cd $LAB/main-wt && git checkout -q --detach origin/main || exit 1
  rm -rf "$OUT"; mkdir -p "$OUT"
  [ -f $LOCAL ] || echo "ВНИМАНИЕ: нет $LOCAL — клиентские сценарии не проверяются" | tee "$OUT/WARN"
  systemctl reset-failed podbor-rules-check 2>/dev/null
  systemd-run --quiet --unit=podbor-rules-check --property=MemoryMax=500M --nice=10 \
    /bin/bash $LAB/repo/server/lab/rules_check.sh "$B" --inside
  echo "запущено: итог — $OUT/REPORT.txt (когда появится $OUT/DONE)"
  exit 0
fi

set -a; . /etc/avtodrug-api.env; set +a
export PYTHONIOENCODING=utf-8
# STATE_DIR не подменять: там справочник брендов (без него «свечи ngk» теряют фирму), веса эмбеддингов и замка.
# Без секрета ссылки на схемы остаются адресами Laximo — проверка ничего не пишет в боевой /var/lib/avtodrug-api
unset TOKEN_SECRET
EVAL=$LAB/repo/server/lab/gold_eval.py     # оценщик — из ветки, код под проверкой — из папки запуска
FILES="app/data/podbor_scenarios.json"
[ -f $LOCAL ] && FILES="$FILES $LOCAL"
for side in main branch; do
  W=$LAB/main-wt; [ $side = branch ] && W=$LAB/repo
  cd $W/server || exit 1
  git -C $W log --oneline -1 > "$OUT/$side.commit"
  $PY $EVAL run $REF "$OUT/gold_$side.json" > "$OUT/gold_$side.txt" 2>&1
  $PY -m app.podbor.scenarios $FILES > "$OUT/scen_$side.txt" 2>&1
done

grep '^✗' "$OUT/scen_main.txt" | sort > "$OUT/fail_main.txt"
grep '^✗' "$OUT/scen_branch.txt" | sort > "$OUT/fail_branch.txt"
NEW=$(comm -13 "$OUT/fail_main.txt" "$OUT/fail_branch.txt")
FIXED=$(comm -23 "$OUT/fail_main.txt" "$OUT/fail_branch.txt")
{
  echo "Ветка: $(cat "$OUT/branch.commit")"
  echo "main:  $(cat "$OUT/main.commit")"
  [ -f "$OUT/WARN" ] && cat "$OUT/WARN"
  echo
  $PY $EVAL diff "$OUT/gold_main.json" "$OUT/gold_branch.json"; GOLD=$?
  echo
  echo "Сценарии, main:  $(tail -1 "$OUT/scen_main.txt")"
  echo "Сценарии, ветка: $(tail -1 "$OUT/scen_branch.txt")"
  if [ -n "$NEW" ]; then
    echo; echo "Новые провалы сценариев (разобрать до выкладки):"
    while IFS= read -r line; do
      echo "$line"; grep -F -A6 -x "$line" "$OUT/scen_branch.txt" | sed -n '2,7p' | grep '^    шаг'
    done <<< "$NEW"
  fi
  [ -n "$FIXED" ] && { echo; echo "Починены сценарии:"; echo "$FIXED"; }
  echo
  if [ $GOLD -eq 0 ] && [ -z "$NEW" ]; then
    echo "ИТОГ: по договору можно — эталон не хуже, новых провалов сценариев нет."
  else
    echo "ИТОГ: РАЗОБРАТЬ — стало хуже (см. выше)."
  fi
} > "$OUT/REPORT.txt" 2>&1
echo done > "$OUT/DONE"
