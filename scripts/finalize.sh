#!/usr/bin/env bash
# Orchestrateur de finalisation des registres NER.
# 1) attend la fin du run de reconciliation mentions>=2 en cours
# 2) reconcilie completement les petits vocabulaires (event/material/technique/
#    artwork) en appliquant les correctifs anachronisme + AAT (re-score depuis cache)
# 3) regenere les 9 registres + rapport
set -u
cd "$(dirname "$0")/.."
LOG=build/ner/finalize.log
echo "[finalize] $(date) — attente fin reconciliation mentions>=2" > "$LOG"

while pgrep -f "reconcile_wikidata.py --min-mentions 2 *$" >/dev/null 2>&1 \
   || pgrep -f "reconcile_wikidata.py --min-mentions 2$" >/dev/null 2>&1; do
  sleep 30
done
# garde-fou : attendre que plus aucun reconcile principal ne tourne
while pgrep -af "reconcile_wikidata.py --min-mentions 2" | grep -v "types" >/dev/null 2>&1; do
  sleep 30
done

echo "[finalize] $(date) — reconciliation principale terminee" >> "$LOG"

# purge les types a re-scorer du fichier resultats (force recalcul avec correctifs)
python3 - >> "$LOG" 2>&1 <<'PY'
import json
f='build/ner/entities_reconciled.json'
d=json.load(open(f,encoding='utf-8'))
keep=[r for r in d if r['type'] not in ('event','material','technique','artwork')]
json.dump(keep,open(f,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"purge: {len(d)-len(keep)} entrees event/material/technique/artwork a recalculer")
PY

echo "[finalize] $(date) — re-reconciliation des petits vocabulaires" >> "$LOG"
python3 scripts/reconcile_wikidata.py --min-mentions 1 --types event,material,technique,artwork >> "$LOG" 2>&1

echo "[finalize] $(date) — generation des registres" >> "$LOG"
python3 scripts/build_registers.py >> "$LOG" 2>&1

date > build/ner/FINALIZED
echo "[finalize] $(date) — TERMINE" >> "$LOG"
