#!/bin/bash
# gePed — mise à jour sur le serveur o2switch, après un « git push » depuis le poste de développement.
# À lancer dans le terminal cPanel (ou en SSH), depuis le dossier du projet :
#     bash deploiement/mise_a_jour.sh
# Étapes : sauvegarde de la base, récupération du code, dépendances, migrations, fichiers statiques,
# contrôle, redémarrage de l'application. S'arrête à la première erreur.
set -euo pipefail
cd "$(dirname "$0")/.."

# Environnement Python créé par « Setup Python App » : ~/virtualenv/<dossier de l'application>/<version>/
# (ex. ~/virtualenv/repositories/GEPED/3.13/bin/activate), chemin affiché en haut de sa page dans cPanel
if [ -z "${VIRTUAL_ENV:-}" ]; then
    APPLICATION="${PWD#"$HOME"/}"
    ACTIVER=$(ls -d "$HOME/virtualenv/$APPLICATION"/*/bin/activate 2>/dev/null | sort -V | tail -n 1 || true)
    if [ -z "$ACTIVER" ]; then
        echo "Environnement Python introuvable : collez d'abord la commande « source … » affichée par Setup Python App."
        exit 1
    fi
    # shellcheck disable=SC1090
    source "$ACTIVER"
fi

echo "1/6 Sauvegarde de la base avant la mise à jour"
bash deploiement/sauvegarde_base.sh

echo "2/6 Récupération du code (git pull)"
git pull --ff-only

echo "3/6 Dépendances"
pip install --quiet -r requirements.txt

echo "4/6 Migrations de la base"
python manage.py migrate --noinput

echo "5/6 Fichiers statiques (CSS, JS, images)"
python manage.py collectstatic --noinput --clear

echo "6/6 Contrôle et redémarrage"
python manage.py check --deploy --fail-level ERROR
mkdir -p tmp && touch tmp/restart.txt
echo "Mise à jour terminée : gePed redémarre (quelques secondes)."
