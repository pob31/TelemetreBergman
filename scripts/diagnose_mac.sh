#!/bin/bash
# Cadreur — collecte de diagnostic, LECTURE SEULE (macOS).
#
#   ./scripts/diagnose_mac.sh
#
# Ne lance PAS Cadreur, n'écrit ni ne supprime rien : chaque démarrage de l'app
# fait tourner les sauvegardes (10 max) et pousserait les bonnes dehors.
# Copie tout l'affichage et envoie-le au support.
set -uo pipefail
cd "$(dirname "$0")/.."

line() { printf '\n===== %s =====\n' "$1"; }

line "MACHINE"
date
sw_vers 2>/dev/null
uptime
echo "dossier : $(pwd)"

line "SPECTACLES (shows/) — le plus important"
ls -la shows/ 2>&1
line "SAUVEGARDES (shows/backups/)"
ls -la shows/backups/ 2>&1

line "VALIDITE DES FICHIERS JSON"
for f in shows/*.json shows/backups/*.json; do
  [ -e "$f" ] || continue
  if ./.venv/bin/python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null; then
    echo "OK      $(wc -c < "$f" | tr -d ' ') octets  $f"
  else
    echo "ABIME   $(wc -c < "$f" | tr -d ' ') octets  $f"
  fi
done

line "DERNIER SPECTACLE OUVERT (cadreur_state.json)"
if [ -f cadreur_state.json ]; then
  wc -c < cadreur_state.json | tr -d ' ' | sed 's/$/ octets/'
  cat cadreur_state.json; echo
else
  echo "ABSENT — l'app a oublié quel spectacle ouvrir (explique une interface vide)"
fi

line "CONFIG (cadreur.toml)"
cat cadreur.toml 2>&1

line "JOURNAL (cadreur_gui.log, 60 dernières lignes)"
tail -60 cadreur_gui.log 2>&1

PORT=$(sed -n 's/^[[:space:]]*port[[:space:]]*=[[:space:]]*\([0-9]\{1,\}\).*/\1/p' cadreur.toml 2>/dev/null | tail -1)
PORT=${PORT:-8080}
line "PORT $PORT — qui l'occupe ?"
lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>&1 || echo "personne n'écoute sur $PORT"

line "CADREUR REPOND-IL ? (si l'app tourne déjà)"
curl -s -m 3 -i "http://127.0.0.1:$PORT/api/health" 2>&1 | head -12 || echo "pas de réponse"

line "ENVIRONNEMENT PYTHON"
./.venv/bin/python3 --version 2>&1
head -1 .venv/bin/cadreur-gui 2>&1
grep -E '^(executable|version) ' .venv/pyvenv.cfg 2>&1

line "VERSION DU CODE"
git log --oneline -3 2>&1
git status -s 2>&1

line "FIN — tout copier et envoyer au support"
