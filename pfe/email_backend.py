"""
Envoi des e-mails en développement : MailHog, avec repli sur des fichiers.

MailHog (https://github.com/mailhog/MailHog) capture tous les e-mails sur un SMTP local
(port 1025 par défaut) et les affiche dans son interface web (http://localhost:8025).
Rien ne part vers de vraies boîtes.

Si MailHog n'est pas lancé, l'e-mail est écrit dans settings.EMAIL_FILE_PATH au lieu
de faire échouer la page (ex. « mot de passe oublié »).
"""
import logging

from django.conf import settings
from django.core.mail.backends.filebased import EmailBackend as FichierBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

logger = logging.getLogger(__name__)


class MailHogBackend(SMTPBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(
            host=settings.GPED_MAILHOG_HOST,
            port=settings.GPED_MAILHOG_PORT,
            username='', password='', use_tls=False, use_ssl=False,
            timeout=3, fail_silently=fail_silently,
        )

    def send_messages(self, email_messages):
        try:
            return super().send_messages(email_messages)
        except OSError as erreur:                       # MailHog arrêté : connexion refusée
            logger.warning("MailHog injoignable (%s:%s, %s) : e-mail écrit dans %s",
                           self.host, self.port, erreur, settings.EMAIL_FILE_PATH)
            return FichierBackend(file_path=settings.EMAIL_FILE_PATH).send_messages(email_messages)
