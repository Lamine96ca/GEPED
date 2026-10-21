# gePed — Gestion Pédagogique

Plateforme Django + Bootstrap 5. Première partie : l'espace administratif (scolarité, départements, programmes).

## Démarrer

```bash
python -m venv env
env\Scripts\python -m pip install -r requirements.txt
env\Scripts\python manage.py migrate
env\Scripts\python manage.py gped_demo --etudiants 10000   # données de démo (optionnel)
env\Scripts\python manage.py runserver
```

Comptes de démo créés par `gped_demo` (mot de passe : `MOT_DE_PASSE_DEMO` dans
`pfe/management/commands/gped_demo.py`) : `admin`, `scolarite`, `departement`, `prog_info`.

## Rôles

Le rôle se déduit des services (`Service`) rattachés au `Personnel` de l'utilisateur :

| Rôle | Condition | Droits |
|---|---|---|
| Scolarité | superutilisateur, ou service de code `SCOL` (`GPED_SCOLARITE_CODE`) | inscrire, réinscrire, modifier, supprimer une inscription, importer, voir et exporter tout |
| Programme | service avec `programme = True` | voir et exporter **uniquement** les étudiants inscrits dans ses programmes |
| Département | autre service (`programme = False`) | voir et exporter la liste (lecture seule) |

Un utilisateur sans service reçoit une erreur 403. Les comptes et les référentiels
(sessions, licences, semestres, promotions, services, personnel) se gèrent dans
`/django-admin/`.

## Liste des étudiants (jusqu'à 10 000 lignes et plus)

Pagination, tri, recherche et filtres côté serveur (`/etudiants/donnees/`, JSON compact
via `values_list`) : le navigateur ne reçoit qu'une page (25 à 500 lignes) et la
dessine en une seule écriture dans le DOM. Les recherches sont différées pendant la
saisie (300 ms) et les requêtes dépassées sont annulées. L'export (Excel ou CSV)
reprend exactement les filtres affichés et respecte le périmètre du rôle.

## Import

`Scolarité → Importer une liste` : fichier `.xlsx` ou `.csv` au format de
`LISTE_DES_ETUDIANT_IMPORTE_2025_2026.xlsx` (modèle téléchargeable sur la page).
Le mode **simulation** vérifie tout sans rien enregistrer. Les étudiants sont rapprochés
par matricule puis par e-mail (mis à jour, jamais dupliqués). Les programmes doivent
exister (nom ou code, insensible aux accents) ; les promotions inconnues sont créées.

## Mot de passe oublié

Lien « Mot de passe oublié ? » sur la page de connexion. L'utilisateur saisit son e-mail
(celui du compte ou de sa fiche personnel) et reçoit un lien valable 2 heures, utilisable une
seule fois. La réponse est identique que l'adresse existe ou non.

- **Développement : MailHog.** Les e-mails sont envoyés au SMTP local de MailHog
  (`localhost:1025`) et se lisent dans son interface : http://localhost:8025. Rien ne part
  vers de vraies boîtes. Lancer MailHog avant de tester (ex. `MailHog.exe`).
  Si MailHog n'est pas lancé, l'e-mail est écrit dans `emails_dev/` (pas d'erreur).
  Hôte/port modifiables avec `GPED_MAILHOG_HOST` / `GPED_MAILHOG_PORT`.
- **Production** : définir `GPED_EMAIL_HOST`, `GPED_EMAIL_PORT`, `GPED_EMAIL_USER`,
  `GPED_EMAIL_PASSWORD` (et `GPED_EMAIL_FROM`) pour un envoi SMTP réel.

## Configuration et secrets

Aucun secret n'est écrit dans le code :

- **Clé secrète** : variable `GPED_SECRET_KEY` en production ; en local, fichier `config/cle_secrete.txt`
  (créé automatiquement au premier lancement).
- **Mode développement** : `GPED_DEBUG=1` par défaut ; en production, `GPED_DEBUG=0`.
- **Base MySQL** : `GPED_DB_NAME`, `GPED_DB_USER`, `GPED_DB_PASSWORD`, `GPED_DB_HOST`, `GPED_DB_PORT`.
- **Domaine** : `GPED_ALLOWED_HOSTS` et `GPED_URL_PUBLIQUE` (QR codes des attestations).

## Git : fichiers jamais suivis

Voir `.gitignore`. Restent sur le poste ou le serveur, à copier à part (FTP) si besoin :
`config/cle_secrete.txt`, `COMPTES_LOCAUX.txt`, les bases et sauvegardes (`db.sqlite3*`, `*.sql`),
`media/` (photos), `prive/` (justificatifs), `ressources/attestation/signature_scolarite.png`
(signature et cachet), `moi/` et `exemples/` (vraies listes, PDF avec cachet), `env/`.

## Tests

```bash
env\Scripts\python manage.py test pfe
```
