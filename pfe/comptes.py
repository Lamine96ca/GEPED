"""
Comptes du personnel : identifiant, invitation par e-mail, statut du compte.

Création d'un compte : le compte est actif mais SANS mot de passe utilisable. L'utilisateur
reçoit un e-mail avec un lien (valable 7 jours, utilisable une seule fois) pour choisir son
mot de passe ; il accède ensuite à GePed selon les droits de ses services (voir pfe/roles.py).
L'invitation peut être renvoyée ; un compte peut être désactivé (il ne peut plus se connecter).
"""
import re
import unicodedata

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes
from django.utils.http import base36_to_int, urlsafe_base64_encode

VALIDITE_INVITATION_JOURS = 7


class JetonInvitation(PasswordResetTokenGenerator):
    """
    Comme le lien « mot de passe oublié », mais valable 7 jours (et non 2 heures) et distinct de
    lui (autre sel). Il ne sert qu'une fois : il change dès que le mot de passe est enregistré.
    """
    key_salt = 'pfe.comptes.JetonInvitation'

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            horodatage = base36_to_int(token.split('-')[0])
        except ValueError:
            return False
        if not any(constant_time_compare(self._make_token_with_timestamp(user, horodatage, secret), token)
                   for secret in [self.secret, *self.secret_fallbacks]):
            return False
        return self._num_seconds(self._now()) - horodatage <= VALIDITE_INVITATION_JOURS * 24 * 3600


jeton_invitation = JetonInvitation()


def _ascii(texte):
    return unicodedata.normalize('NFKD', str(texte or '')).encode('ascii', 'ignore').decode().lower()


def identifiant_propose(prenom, nom):
    """« Mariame CAMARA » → « mariame.camara » (puis mariame.camara2… s'il est déjà pris)."""
    base = '.'.join(p for p in (re.sub(r'[^a-z0-9]', '', _ascii(prenom.split()[0] if prenom.split() else '')),
                                re.sub(r'[^a-z0-9]', '', _ascii(nom))) if p)[:140] or 'agent'
    identifiant, n = base, 1
    while User.objects.filter(username__iexact=identifiant).exists():
        n += 1
        identifiant = f"{base}{n}"
    return identifiant


def synchroniser_email(user, email, sauf=None):
    """Même adresse partout pour une personne : compte, fiche personnel et fiche enseignant (un seul e-mail)."""
    from .models import Enseignant, Personnel
    if user.email != email:
        user.email = email
        user.save(update_fields=['email'])
    for fiche in (Personnel.objects.filter(utilisateur=user).first(), Enseignant.objects.filter(utilisateur=user).first()):
        if fiche is not None and fiche is not sauf and fiche.email != email:
            fiche.email = email
            fiche.save(update_fields=['email'])


def statut(user):
    """('actif' | 'invite' | 'desactive', libellé)."""
    if not user.is_active:
        return 'desactive', 'Désactivé'
    if not user.has_usable_password():
        return 'invite', 'Invitation en attente'
    return 'actif', 'Actif'


def _adresse_site(request):
    base = getattr(settings, 'GPED_URL_PUBLIQUE', '')
    return base or f"{request.scheme}://{request.get_host()}"


def envoyer_lien(request, user, invitation=True):
    """
    Envoie l'e-mail d'invitation (compte créé) ou un lien de réinitialisation du mot de passe.
    Renvoie l'adresse utilisée. Lève une exception si l'envoi échoue.
    """
    from .roles import _calculer_profil
    personnel = getattr(user, 'profil_personnel', None)
    destinataire = (personnel.email if personnel else '') or user.email
    if not destinataire:
        raise ValueError("Ce compte n'a pas d'adresse e-mail.")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    if invitation:
        chemin = reverse('compte_activer', args=[uid, jeton_invitation.make_token(user)])
    else:
        chemin = reverse('password_reset_confirm', args=[uid, PasswordResetTokenGenerator().make_token(user)])
    site = _adresse_site(request)
    profil = _calculer_profil(user)
    contexte = {
        'user': user, 'personnel': personnel, 'lien': f"{site}{chemin}", 'site': site, 'invitation': invitation,
        'role': profil.libelle, 'services': profil.structure or ', '.join(s.nom for s in (personnel.service.all()
                                                                                          if personnel else [])),
        'validite': f"{VALIDITE_INVITATION_JOURS} jours" if invitation else "2 heures",
        'logo': f"{site}{settings.STATIC_URL}pfe/img/logo_ist.png",
    }
    sujet = ("Votre compte GePed a été créé" if invitation else "GePed : réinitialisation de votre mot de passe")
    message = EmailMultiAlternatives(sujet, render_to_string('pfe/emails/invitation.txt', contexte),
                                     settings.DEFAULT_FROM_EMAIL, [destinataire])
    message.attach_alternative(render_to_string('pfe/emails/invitation.html', contexte), 'text/html')
    message.send()
    return destinataire


DIRECTION_ETUDES = "la Direction des Études"


def envoyer_mail_enseignant(request, enseignant, quoi='activation'):
    """
    E-mail de la Direction des Études à un enseignant. `quoi` :
      - 'activation'    : compte pas encore activé → lien pour choisir le mot de passe (7 jours) ;
      - 'acces'         : compte déjà actif (rattaché à son compte existant) → simple information, pas de lien ;
      - 'reinitialiser' : lien pour un nouveau mot de passe (2 heures).
    Le texte dit clairement si la personne avait déjà un compte : dans ce cas, il ne sert à rien d'en créer un autre.
    Renvoie l'adresse utilisée ; lève une exception si l'envoi échoue.
    """
    from .roles import _calculer_profil
    user = enseignant.utilisateur
    destinataire = enseignant.email or user.email
    if not destinataire:
        raise ValueError("Cet enseignant n'a pas d'adresse e-mail.")
    if quoi == 'activation' and user.has_usable_password():
        quoi = 'acces'                                      # déjà activé : on informe seulement
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    site = _adresse_site(request)
    if quoi == 'activation':
        lien = site + reverse('compte_activer', args=[uid, jeton_invitation.make_token(user)])
    elif quoi == 'reinitialiser':
        lien = site + reverse('password_reset_confirm', args=[uid, PasswordResetTokenGenerator().make_token(user)])
    else:
        lien = site + reverse('login')
    personnel = getattr(user, 'profil_personnel', None)
    existant = enseignant.compte_existant
    contexte = {
        'user': user, 'enseignant': enseignant, 'personnel': personnel, 'existant': existant, 'quoi': quoi,
        'lien': lien, 'site': site, 'expediteur': DIRECTION_ETUDES, 'role': _calculer_profil(user).libelle,
        'validite': f"{VALIDITE_INVITATION_JOURS} jours" if quoi == 'activation' else "2 heures",
        'logo': f"{site}{settings.STATIC_URL}pfe/img/logo_ist.png",
    }
    if quoi == 'reinitialiser':
        sujet = "GePed : réinitialisation de votre mot de passe"
    elif existant:
        sujet = "GePed : votre accès enseignant, sur votre compte existant"
    else:
        sujet = "GePed : votre compte enseignant a été créé"
    message = EmailMultiAlternatives(sujet, render_to_string('pfe/emails/enseignant.txt', contexte),
                                     settings.DEFAULT_FROM_EMAIL, [destinataire])
    message.attach_alternative(render_to_string('pfe/emails/enseignant.html', contexte), 'text/html')
    message.send()
    return destinataire
