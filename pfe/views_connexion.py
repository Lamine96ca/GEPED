"""
Connexion en deux étapes (double facteur) : identifiant + mot de passe, puis un code à 6 chiffres envoyé par
e-mail, à saisir pour ouvrir la session. Rien n'est ouvert avant le code : l'utilisateur en attente est gardé
dans la session anonyme (le code y est stocké haché, jamais en clair).

- Code valable GPED_CODE_CONNEXION_MINUTES minutes (10 par défaut), 5 essais au plus, puis il faut recommencer.
- « Renvoyer le code » : un nouveau code au plus toutes les 60 secondes, 3 renvois au plus.
- TOUS les comptes reçoivent un code, sauf les super utilisateurs (connexion avec le seul mot de passe).
- Compte sans adresse e-mail utilisable (vide, ou fictive en .local hors développement) : connexion refusée, avec
  un message pour contacter l'administrateur (qui ajoute l'adresse dans « Personnel » ou « Enseignants »).
- Interrupteur d'urgence : GPED_DOUBLE_FACTEUR=0 dans le .env coupe le code pour tout le monde.
"""
import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.views import LoginView
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters

logger = logging.getLogger(__name__)

CLE = 'connexion_en_attente'
ESSAIS_MAX = 5
RENVOIS_MAX = 3
DELAI_RENVOI = 60          # secondes entre deux envois


def _minutes():
    return getattr(settings, 'GPED_CODE_CONNEXION_MINUTES', 10)


def email_utilisable(user):
    """Adresse à laquelle envoyer le code, ou '' (vide, ou fictive en .local). En développement, une adresse
    .local est acceptée : le code est écrit dans emails_dev/ (ou MailHog), rien ne part."""
    email = (user.email or '').strip()
    if not email or '@' not in email or (email.lower().endswith('.local') and not settings.DEBUG):
        return ''
    return email


def _empreinte(code, user_id, sel):
    message = f'{user_id}:{sel}:{code}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def masquer(email):
    """l••••••a@gmail.com : assez pour reconnaître son adresse, sans l'afficher en entier."""
    nom, _, domaine = email.partition('@')
    if len(nom) <= 2:
        return f'{nom[:1]}•••@{domaine}'
    return f'{nom[0]}{"•" * min(len(nom) - 2, 6)}{nom[-1]}@{domaine}'


def _envoyer_code(request, user, email):
    """Nouveau code : envoyé par e-mail, son empreinte gardée en session. Renvoie False si l'envoi a échoué."""
    code = f'{secrets.randbelow(10 ** 6):06d}'
    sel = secrets.token_hex(8)
    attente = request.session.get(CLE, {})
    contexte = {'user': user, 'code': code, 'minutes': _minutes(), 'protocol': request.scheme,
                'domain': request.get_host()}
    message = EmailMultiAlternatives(
        subject=f'GePed : votre code de connexion {code}',
        body=render_to_string('pfe/emails/code_connexion.txt', contexte),
        to=[email])
    message.attach_alternative(render_to_string('pfe/emails/code_connexion.html', contexte), 'text/html')
    try:
        message.send()
    except Exception:                                           # SMTP injoignable, refus du serveur…
        logger.exception("Code de connexion : envoi impossible à %s (utilisateur %s)", email, user.pk)
        return False
    maintenant = time.time()
    attente.update({'empreinte': _empreinte(code, user.pk, sel), 'sel': sel, 'expire': maintenant + 60 * _minutes(),
                    'essais': 0, 'envoye': maintenant})
    request.session[CLE] = attente
    return True


class Connexion(LoginView):
    """Étape 1 : identifiant et mot de passe (même page qu'avant). Si le double facteur s'applique, aucun accès :
    un code est envoyé et l'on passe à l'étape 2."""
    template_name = 'pfe/connexion.html'
    redirect_authenticated_user = True
    erreur_envoi = False
    sans_email = False
    presentation_terminee = False

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'erreur_envoi': self.erreur_envoi, 'sans_email': self.sans_email,
                'presentation_terminee': self.presentation_terminee}

    def form_valid(self, form):
        user = form.get_user()
        personnel = getattr(user, 'profil_personnel', None)
        if personnel is not None and personnel.presentation and personnel.presentation_expiree:
            user.is_active = False                              # compte de présentation : l'heure est passée
            user.save(update_fields=['is_active'])
            form.add_error(None, "Ce compte de présentation est fermé (une heure après sa première connexion).")
            self.presentation_terminee = True
            return self.form_invalid(form)
        if not getattr(settings, 'GPED_DOUBLE_FACTEUR', True) or user.is_superuser:
            return super().form_valid(form)                  # super utilisateur : connexion directe
        email = email_utilisable(user)
        if not email:
            self.sans_email = True                           # pas d'adresse pour le code : connexion refusée
            return self.form_invalid(form)
        self.request.session.pop(CLE, None)
        self.request.session[CLE] = {'user_id': user.pk, 'backend': user.backend, 'suivant': self.get_success_url(),
                                     'renvois': 0}
        if not _envoyer_code(self.request, user, email):
            self.request.session.pop(CLE, None)
            self.erreur_envoi = True
            return self.form_invalid(form)
        return redirect('connexion_code')


@sensitive_post_parameters('code')
@csrf_protect
@never_cache
def connexion_code(request):
    """Étape 2 : saisie du code reçu par e-mail ; « Renvoyer le code » ; « Recommencer »."""
    attente = request.session.get(CLE)
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    if not attente or 'empreinte' not in attente:
        return redirect('login')
    user = get_user_model().objects.filter(pk=attente['user_id'], is_active=True).first()
    if user is None:
        request.session.pop(CLE, None)
        return redirect('login')
    email = email_utilisable(user)
    erreur = None
    if request.method == 'POST' and 'renvoyer' in request.POST:
        attendre = int(DELAI_RENVOI - (time.time() - attente.get('envoye', 0)))
        if attente.get('renvois', 0) >= RENVOIS_MAX:
            erreur = "Nombre maximal de renvois atteint : recommencez la connexion."
        elif attendre > 0:
            erreur = f"Patientez encore {attendre} seconde{'s' if attendre > 1 else ''} avant de demander un nouveau code."
        else:
            attente['renvois'] = attente.get('renvois', 0) + 1
            request.session[CLE] = attente
            if _envoyer_code(request, user, email):
                messages.success(request, f"Un nouveau code a été envoyé à {masquer(email)}.")
                return redirect('connexion_code')
            erreur = "Le code n'a pas pu être envoyé. Réessayez dans un instant."
    elif request.method == 'POST':
        saisi = ''.join(c for c in request.POST.get('code', '') if c.isdigit())
        if time.time() > attente['expire']:
            request.session.pop(CLE, None)
            messages.error(request, "Le code a expiré. Reconnectez-vous pour en recevoir un nouveau.")
            return redirect('login')
        attente['essais'] = attente.get('essais', 0) + 1
        request.session[CLE] = attente
        bon = len(saisi) == 6 and hmac.compare_digest(_empreinte(saisi, user.pk, attente['sel']), attente['empreinte'])
        if bon:
            suivant = attente.get('suivant') or settings.LOGIN_REDIRECT_URL
            request.session.pop(CLE, None)
            user.backend = attente['backend']
            auth_login(request, user, backend=attente['backend'])    # change aussi la clé de session
            if not url_has_allowed_host_and_scheme(suivant, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
                suivant = reverse(settings.LOGIN_REDIRECT_URL)
            return redirect(suivant)
        if attente['essais'] >= ESSAIS_MAX:
            request.session.pop(CLE, None)
            messages.error(request, "Trop d'essais avec un code incorrect. Reconnectez-vous pour recevoir un nouveau code.")
            return redirect('login')
        reste = ESSAIS_MAX - attente['essais']
        erreur = f"Code incorrect. Il vous reste {reste} essai{'s' if reste > 1 else ''}."
    return render(request, 'pfe/connexion_code.html', {
        'email_masque': masquer(email), 'erreur': erreur, 'minutes': _minutes(),
        'renvoi_possible': attente.get('renvois', 0) < RENVOIS_MAX,
    })


def recommencer_connexion(request):
    """Abandonne la connexion en attente (mauvais compte, e-mail non reçu…) et revient à l'étape 1."""
    request.session.pop(CLE, None)
    return redirect('login')


def admin_vers_connexion(request):
    """La page de connexion de l'administration Django passerait outre le code : on la renvoie vers celle de GePed."""
    from urllib.parse import urlencode
    suivant = request.GET.get('next') or reverse('admin:index')
    return redirect(f"{reverse('login')}?{urlencode({'next': suivant})}")

