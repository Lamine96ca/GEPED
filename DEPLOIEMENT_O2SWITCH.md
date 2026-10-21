# Héberger gePed chez o2switch

Guide pas à pas pour mettre gePed en ligne sur un hébergement **o2switch** (cPanel), puis le mettre à jour.
Exemples avec le domaine `geped.gbelya-tech.com` et l'identifiant cPanel `moncompte` : remplacez-les par les vôtres.

## Ce que gePed utilise chez o2switch

| Besoin | Outil o2switch (cPanel) |
|---|---|
| Application Django | **Setup Python App** (Passenger) + `passenger_wsgi.py` |
| Base de données | **Bases de données MySQL** + phpMyAdmin |
| HTTPS | **AutoSSL** (Let's Encrypt, gratuit et automatique) |
| E-mails (invitations, mot de passe oublié) | **Comptes de messagerie** (SMTP du domaine) |
| Code | **Git Version Control** (depuis le dépôt privé GitHub) |
| Commandes | **Terminal** (ou SSH) |
| Sauvegardes et maintenance | **Tâches Cron** + JetBackup d'o2switch |

Tous les réglages se font dans un fichier **`.env`** sur le serveur (modèle : [`.env.exemple`](.env.exemple)). Rien de secret n'est dans Git.

---

## 1. Préparer le domaine et le HTTPS

1. cPanel → **Domaines** : créez le sous-domaine `geped.gbelya-tech.com`. Notez son **dossier racine**, par exemple `/home/moncompte/geped.gbelya-tech.com`. C'est le dossier public, servi par Apache.
2. cPanel → **SSL/TLS Status** : lancez **AutoSSL** pour ce domaine, et attendez que le cadenas soit vert.

## 2. Créer la base MySQL

1. cPanel → **Bases de données MySQL** :
   - créez la base `moncompte_geped` ;
   - créez l'utilisateur `moncompte_geped`, avec un mot de passe long ;
   - ajoutez l'utilisateur à la base avec **tous les privilèges**.
2. Sur le PC (WAMP lancé), exportez la base locale :
   ```
   powershell -File deploiement\exporter_base_wamp.ps1
   ```
   Le fichier est créé dans `sauvegardes\`, un dossier exclu de Git.
3. cPanel → **phpMyAdmin** : sélectionnez `moncompte_geped`, puis **Importer**, et choisissez ce fichier. S'il est trop gros pour phpMyAdmin, envoyez-le par FTP, puis lancez dans le Terminal : `mysql -u moncompte_geped -p moncompte_geped < fichier.sql`.

## 3. Créer la boîte e-mail d'envoi

1. cPanel → **Comptes de messagerie** : créez `no-reply@gbelya-tech.com`.
2. Ouvrez **Connecter des appareils** pour cette boîte : notez le **serveur SMTP** (souvent `mail.gbelya-tech.com`), le **port 465 (SSL)**, l'identifiant (l'adresse complète) et le mot de passe.
3. cPanel → **Délivrabilité des e-mails** : vérifiez que **SPF** et **DKIM** sont « valides ». Corrigez-les si besoin : c'est ce qui évite que les invitations arrivent dans les indésirables.
4. Si le domaine `gbelya-tech.com` est géré ailleurs (DNS chez un autre fournisseur), ajoutez-y les enregistrements SPF et DKIM proposés par cPanel.

## 4. Récupérer le code

Le dépôt GitHub est privé : le serveur a besoin d'une **clé de déploiement**, en lecture seule.

1. cPanel → **Accès SSH** → **Gérer les clés SSH** : générez une clé (par exemple `geped_github`), puis affichez la clé **publique**.
2. GitHub → dépôt `GEPED` → **Settings → Deploy keys → Add deploy key** : collez la clé publique, **sans** cocher « Allow write access ».
3. cPanel → **Git Version Control** → **Créer** :
   - URL de clonage : `git@github.com:Lamine96ca/GEPED.git` ;
   - chemin : `/home/moncompte/geped`. C'est la racine de l'application, **hors** du dossier public.

## 5. Créer l'application Python

cPanel → **Setup Python App** → **Create Application** :

| Champ | Valeur |
|---|---|
| Python version | la plus récente proposée (**3.10 au minimum**) |
| Application root | `geped` |
| Application URL | `geped.gbelya-tech.com` |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Cliquez sur **Create**. En haut de la page, cPanel affiche une commande `source /home/moncompte/virtualenv/geped/3.x/bin/activate && cd /home/moncompte/geped` : copiez-la.

## 6. Installer et configurer (Terminal)

cPanel → **Terminal** :

```bash
source /home/moncompte/virtualenv/geped/3.x/bin/activate && cd /home/moncompte/geped
pip install --upgrade pip
pip install -r requirements.txt
```

> Le pilote MySQL est **PyMySQL** (100 % Python) : il s'installe sans compilation. `mysqlclient`, lui, ne se compile pas chez o2switch (« Failed to build installable wheels… mysqlclient ») : il n'est plus dans `requirements.txt`.

Créez le fichier `.env` à partir du modèle, puis remplissez-le avec l'éditeur de fichiers de cPanel :

```bash
cp .env.exemple .env
chmod 600 .env
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

Collez la clé affichée dans `GPED_SECRET_KEY`. Renseignez ensuite :
- la base : `GPED_DB_*` ;
- les e-mails : `GPED_EMAIL_*` ;
- le domaine : `GPED_ALLOWED_HOSTS` et `GPED_URL_PUBLIQUE` ;
- les dossiers publics : `GPED_STATIC_ROOT=/home/moncompte/geped.gbelya-tech.com/static` et `GPED_MEDIA_ROOT=/home/moncompte/geped.gbelya-tech.com/media`.

Puis :

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
mkdir -p /home/moncompte/geped.gbelya-tech.com/media
cp deploiement/media.htaccess /home/moncompte/geped.gbelya-tech.com/media/.htaccess
```

`check --deploy` ne doit afficher **aucune erreur**. Un avertissement sur HSTS est normal tant que `GPED_HSTS_SECONDES=0`.

## 7. Envoyer les fichiers qui ne sont pas dans Git

Avec le **Gestionnaire de fichiers** de cPanel (ou FTP), envoyez :

| Depuis le PC | Vers le serveur |
|---|---|
| `ressources/attestation/signature_scolarite.png` (signature et cachet) | `/home/moncompte/geped/ressources/attestation/` |
| `media/etudiant/photo/…` (photos des étudiants) | `/home/moncompte/geped.gbelya-tech.com/media/etudiant/photo/` |
| `prive/…` (justificatifs, s'il y en a) | `/home/moncompte/geped/prive/` |

Les images du cadre des attestations et l'en-tête des relevés sont déjà dans Git.

## 8. Démarrer et vérifier

1. **Setup Python App** → **Restart**, puis ouvrez `https://geped.gbelya-tech.com`.
2. Connectez-vous avec un compte super utilisateur existant (repris de la base importée), ou créez-en un : `python manage.py createsuperuser`.
3. Testez l'envoi d'e-mails :
   ```bash
   python manage.py sendtestemail votre.adresse@exemple.com
   ```
4. Menu **Personnel** : créez un compte de test. Vérifiez que l'invitation arrive, que le lien s'ouvre en `https://…` et que l'activation connecte bien l'utilisateur.
5. Imprimez une attestation et un relevé : le **QR code** doit ouvrir la page de vérification sur `https://geped.gbelya-tech.com/…`.
6. Une fois que tout fonctionne en HTTPS, passez `GPED_HSTS_SECONDES=31536000` dans `.env`, puis redémarrez.

## 9. Tâches automatiques (cPanel → Tâches Cron)

| Quand | Commande |
|---|---|
| Chaque nuit à 2 h | `bash /home/moncompte/geped/deploiement/sauvegarde_base.sh >/dev/null 2>&1` |
| Chaque dimanche à 3 h | `cd /home/moncompte/geped && /home/moncompte/virtualenv/geped/3.x/bin/python manage.py clearsessions` |

Les sauvegardes (base et photos) vont dans `~/sauvegardes_geped`, hors du dossier public. Les 30 dernières sont gardées. o2switch fait aussi ses propres sauvegardes (cPanel → **JetBackup**).

## 10. Mettre à jour gePed

Sur le PC : `git push` (ou demandez-le à Claude). Ensuite, dans le Terminal du serveur :

```bash
source /home/moncompte/virtualenv/geped/3.x/bin/activate && cd /home/moncompte/geped
bash deploiement/mise_a_jour.sh
```

Le script enchaîne :
1. la **sauvegarde** de la base ;
2. `git pull` ;
3. les dépendances ;
4. les **migrations** ;
5. `collectstatic` ;
6. le contrôle de sécurité ;
7. le **redémarrage** de l'application.

## En cas de problème

| Symptôme | Où regarder / que faire |
|---|---|
| Page « erreur 500 » | `logs/gped.log` dans le dossier du projet ; les `GPED_ADMINS` reçoivent aussi l'erreur par e-mail |
| « Bad Request (400) » | le domaine manque dans `GPED_ALLOWED_HOSTS` |
| « Forbidden : CSRF » à la connexion | `GPED_URL_PUBLIQUE` doit être exactement l'adresse du site, en `https://` |
| Site sans mise en forme | `collectstatic` non lancé, ou `GPED_STATIC_ROOT` hors du dossier public du domaine |
| Photos absentes | dossier `media/` du domaine, et `GPED_MEDIA_ROOT` |
| E-mails non reçus | `python manage.py sendtestemail …` ; identifiants SMTP ; SPF et DKIM dans « Délivrabilité des e-mails » |
| L'application ne redémarre pas | Setup Python App → **Restart**, ou `touch tmp/restart.txt` |

## Bon à savoir

- **Police de l'attestation de fin d'études :** Century Gothic n'existe pas sur le serveur Linux. L'attestation s'imprime alors en Helvetica, ce qui est prévu par le code. Pour garder Century Gothic, il faudrait une licence permettant de l'installer sur le serveur.
- **Mode développement :** il reste celui du PC (WAMP, MailHog). Le fichier `.env` n'existe que sur le serveur.
- **Sécurité :** ne mettez jamais `GPED_DEBUG=1` sur le serveur. Gardez `.env` en droits `600`. Changez les mots de passe des comptes de démonstration (`admin`, `scolarite`…) ou désactivez ces comptes avant l'ouverture.
