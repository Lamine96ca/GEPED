"""
Diagnostic des e-mails gePed (surtout en production) :

    python manage.py gped_test_email destinataire@exemple.com

Teste une à une les étapes de l'envoi SMTP et explique en français ce qui bloque : réglages lus
dans .env, nom du serveur, connexion, chiffrement SSL/TLS (certificat), identifiants, envoi.
Le mot de passe n'est jamais affiché.
"""
import smtplib
import socket
import ssl

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Teste l'envoi d'un e-mail et explique ce qui bloque (serveur, chiffrement, identifiants)."

    def add_arguments(self, parser):
        parser.add_argument('destinataire', help="Adresse qui recevra l'e-mail de test")

    def etape(self, texte):
        self.stdout.write(f"\n• {texte}")

    def ok(self, texte):
        self.stdout.write(self.style.SUCCESS(f"  OK : {texte}"))

    def echec(self, texte, conseil):
        self.stdout.write(self.style.ERROR(f"  ÉCHEC : {texte}"))
        raise CommandError(conseil)

    def handle(self, destinataire, **options):
        backend = settings.EMAIL_BACKEND
        self.etape("Réglages lus (fichier .env)")
        if not backend.endswith('smtp.EmailBackend'):
            self.stdout.write(f"  Mode : {backend}")
            self.stdout.write("  Pas de serveur SMTP configuré (GPED_EMAIL_HOST vide) : en développement, les e-mails "
                              "vont dans MailHog ou dans le dossier emails_dev/. En production, remplissez "
                              "GPED_EMAIL_HOST dans .env puis redémarrez l'application.")
            return
        hote, port = settings.EMAIL_HOST, settings.EMAIL_PORT
        ssl_direct, starttls = settings.EMAIL_USE_SSL, settings.EMAIL_USE_TLS
        utilisateur, mot_de_passe = settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD
        self.stdout.write(f"  Serveur : {hote}   port : {port}   chiffrement : "
                          f"{'SSL' if ssl_direct else 'STARTTLS' if starttls else 'aucun'}")
        self.stdout.write(f"  Identifiant : {utilisateur or '(vide)'}   mot de passe : "
                          f"{'renseigné (' + str(len(mot_de_passe)) + ' caractères)' if mot_de_passe else '(VIDE)'}")
        self.stdout.write(f"  Expéditeur : {settings.DEFAULT_FROM_EMAIL}")
        if not utilisateur or not mot_de_passe:
            self.echec("identifiant ou mot de passe vide",
                       "Renseignez GPED_EMAIL_USER (adresse complète) et GPED_EMAIL_PASSWORD dans .env, "
                       "sans espace ni guillemets autour du « = », puis redémarrez l'application.")
        if ssl_direct and port != 465 or starttls and port == 465:
            self.stdout.write(self.style.WARNING("  Attention : 465 va avec GPED_EMAIL_SSL=1, 587 avec GPED_EMAIL_TLS=1."))

        self.etape(f"Nom du serveur « {hote} »")
        try:
            adresses = sorted({a[4][0] for a in socket.getaddrinfo(hote, port)})
            self.ok(', '.join(adresses))
        except socket.gaierror:
            self.echec("ce nom de serveur n'existe pas",
                       "Vérifiez GPED_EMAIL_HOST : cPanel → Comptes de messagerie → « Connecter des appareils » "
                       "→ « Serveur sortant ».")

        self.etape(f"Connexion et chiffrement ({'SSL' if ssl_direct else 'STARTTLS' if starttls else 'aucun'})")
        contexte = ssl.create_default_context()
        try:
            if ssl_direct:
                smtp = smtplib.SMTP_SSL(hote, port, timeout=20, context=contexte)
            else:
                smtp = smtplib.SMTP(hote, port, timeout=20)
                smtp.ehlo()
                if starttls:
                    smtp.starttls(context=contexte)
                    smtp.ehlo()
            self.ok("connexion établie")
        except ssl.SSLCertVerificationError as e:
            self.echec(f"certificat refusé ({e.verify_message})",
                       f"Le certificat SSL ne correspond pas au nom « {hote} ». Mettez dans GPED_EMAIL_HOST le nom "
                       "du serveur indiqué par cPanel dans « Connecter des appareils » → « Paramètres SSL/TLS "
                       "sécurisés » (souvent un nom en …o2switch.net), ou lancez AutoSSL pour mail.<votre domaine>.")
        except (socket.timeout, TimeoutError):
            self.echec("délai dépassé", "Le port est sans doute bloqué ou erroné : essayez 465 avec GPED_EMAIL_SSL=1, "
                                        "ou 587 avec GPED_EMAIL_SSL=0 et GPED_EMAIL_TLS=1.")
        except (ConnectionRefusedError, smtplib.SMTPServerDisconnected, ssl.SSLError, OSError) as e:
            self.echec(f"connexion impossible ({e})",
                       "Vérifiez le port et le chiffrement : 465 + GPED_EMAIL_SSL=1, ou 587 + GPED_EMAIL_TLS=1.")

        self.etape(f"Identification de « {utilisateur} »")
        try:
            smtp.login(utilisateur, mot_de_passe)
            self.ok("identifiants acceptés")
        except smtplib.SMTPAuthenticationError:
            smtp.close()
            self.echec("identifiant ou mot de passe refusé",
                       "GPED_EMAIL_USER doit être l'adresse COMPLÈTE (ex. no-reply@gbelya-tech.com) et "
                       "GPED_EMAIL_PASSWORD le mot de passe de cette boîte (cPanel → Comptes de messagerie → "
                       "Gérer → changer le mot de passe si besoin).")
        except smtplib.SMTPException as e:
            smtp.close()
            self.echec(f"identification impossible ({e})", "Vérifiez le chiffrement (SSL/TLS) et les identifiants.")
        smtp.quit()

        self.etape(f"Envoi d'un e-mail de test à {destinataire} (par gePed)")
        try:
            EmailMessage("gePed : e-mail de test", "Si vous lisez ce message, l'envoi des e-mails de gePed fonctionne.",
                         settings.DEFAULT_FROM_EMAIL, [destinataire]).send()
        except smtplib.SMTPSenderRefused:
            self.echec("expéditeur refusé",
                       "GPED_EMAIL_FROM doit utiliser la MÊME adresse que GPED_EMAIL_USER "
                       "(ex. gePed - IST de Mamou <no-reply@gbelya-tech.com>).")
        except Exception as e:
            self.echec(f"envoi impossible ({e})", "Vérifiez GPED_EMAIL_FROM et le destinataire.")
        self.ok("e-mail envoyé")
        self.stdout.write(self.style.SUCCESS(
            "\nTout fonctionne. Si l'e-mail n'arrive pas : regardez les indésirables, puis cPanel → "
            "« Délivrabilité des e-mails » (SPF et DKIM doivent être valides) et « Suivi de la livraison »."))
