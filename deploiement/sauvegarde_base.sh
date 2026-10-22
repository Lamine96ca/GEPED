#!/bin/bash
# gePed — sauvegarde de la base MySQL (et des fichiers déposés) chez o2switch.
# Manuellement :   bash deploiement/sauvegarde_base.sh
# Chaque nuit :    cPanel → Tâches Cron, ex. à 2 h :
#                  0 2 * * * bash $HOME/pfe2/deploiement/sauvegarde_base.sh >/dev/null 2>&1
# Les sauvegardes vont dans ~/sauvegardes_geped (HORS du dossier public) ; les 30 dernières sont gardées.
# Le mot de passe est lu dans le fichier .env (jamais écrit sur la ligne de commande).
set -euo pipefail
cd "$(dirname "$0")/.."

lire() { grep -E "^$1=" .env | head -n 1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
BASE=$(lire GPED_DB_NAME); UTILISATEUR=$(lire GPED_DB_USER); MOT_DE_PASSE=$(lire GPED_DB_PASSWORD)
MEDIA="$(pwd)/media"                         # photos : media/ dans le dossier du projet (config/settings.py)
DOSSIER="$HOME/sauvegardes_geped"
mkdir -p "$DOSSIER"
chmod 700 "$DOSSIER"
HORODATAGE=$(date +%Y%m%d_%H%M%S)

# Fichier d'options temporaire (lisible par le seul propriétaire) : le mot de passe n'apparaît pas dans « ps »
OPTIONS=$(mktemp)
trap 'rm -f "$OPTIONS"' EXIT
chmod 600 "$OPTIONS"
printf '[client]\nuser=%s\npassword=%s\n' "$UTILISATEUR" "$MOT_DE_PASSE" > "$OPTIONS"

mysqldump --defaults-extra-file="$OPTIONS" --single-transaction --routines --default-character-set=utf8mb4 "$BASE" \
    | gzip > "$DOSSIER/base_${HORODATAGE}.sql.gz"
# Photos des étudiants et justificatifs (prive/) : archive séparée
tar -czf "$DOSSIER/fichiers_${HORODATAGE}.tar.gz" -C / "${MEDIA#/}" "$(pwd | sed 's#^/##')/prive" 2>/dev/null || true

# Ne garder que les 30 dernières sauvegardes de chaque type
ls -1t "$DOSSIER"/base_*.sql.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
ls -1t "$DOSSIER"/fichiers_*.tar.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
echo "Sauvegarde : $DOSSIER/base_${HORODATAGE}.sql.gz"
