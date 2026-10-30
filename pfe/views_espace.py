"""
Espace étudiant (public, sans compte) : /etudiant/
  - Inscription   : préinscrit, INE + PV du bac → contacts, photo, pièces (facultatives).
  - Réinscription : ancien étudiant, INE + matricule → contacts, niveau, bourse, formation,
                    relevé de notes de la dernière année.
  - Master        : adresse e-mail → lien personnel par e-mail → inscription (toutes les pièces de
                    l'appel à candidature) ou réinscription (pièces de base encore manquantes).
La page d'accueil explique chaque démarche ; chacune a ensuite sa page d'identification.
Rien n'est appliqué à la fiche avant la validation par la scolarité (voir pfe/espace_etudiant.py).
"""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import espace_etudiant as espace
from . import pieces as dossier
from .attestations import _nom_departement
from .forms import (DossierEtudiantForm, IdentificationInscriptionForm, IdentificationMasterForm,
                    IdentificationReinscriptionForm)
from .models import DemandeEtudiant, Etudiant, Inscription, PieceDossier
from .photos import url_photo

ENVOI_MAX = 40 * 1024 * 1024           # taille maximale d'un envoi du dossier (photos déjà réduites : ≈ 2 à 5 Mo)

# En-têtes de sécurité des pages publiques de l'espace : rien ne vient d'un autre site, pas de script en
# ligne, pas d'intégration dans un cadre, formulaires envoyés seulement à gePed, pas de cache.
CSP_ESPACE = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' blob:; "
              "media-src 'self' blob: mediastream:; object-src 'none'; frame-src 'none'; worker-src 'none'; "
              "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def securise(vue):
    @wraps(vue)
    def enveloppe(request, *args, **kwargs):
        reponse = vue(request, *args, **kwargs)
        reponse['Content-Security-Policy'] = CSP_ESPACE
        reponse['X-Frame-Options'] = 'DENY'
        reponse['X-Content-Type-Options'] = 'nosniff'
        reponse['Referrer-Policy'] = 'same-origin'
        reponse['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=(), payment=(), usb=()'
        reponse['Cross-Origin-Opener-Policy'] = 'same-origin'
        reponse['X-Robots-Tag'] = 'noindex, nofollow'
        return reponse
    return enveloppe


# Pièces demandées dans l'espace étudiant : TOUTES obligatoires
PIECES = {
    DemandeEtudiant.INSCRIPTION: [PieceDossier.ACTE_NAISSANCE, PieceDossier.DIPLOME_BAC, PieceDossier.RELEVE_BAC],
    DemandeEtudiant.REINSCRIPTION: [PieceDossier.RELEVE_NOTES],
}
OBLIGATOIRES = PIECES
# Master : pièces de base, puis celles de l'appel à candidature pour une inscription, le relevé de l'année
# dernière (facultatif) pour une réinscription. Une pièce déjà dans le dossier n'est pas redemandée.
PIECES_BASE_MASTER = [PieceDossier.ACTE_NAISSANCE, PieceDossier.DIPLOME_BAC, PieceDossier.RELEVE_BAC,
                      PieceDossier.DIPLOME_LICENCE]
PIECES_MASTER = {
    DemandeEtudiant.INSCRIPTION: PIECES_BASE_MASTER + [
        PieceDossier.RELEVES_LICENCE, PieceDossier.DEMANDE_MANUSCRITE, PieceDossier.LETTRE_MOTIVATION,
        PieceDossier.PROJET_RECHERCHE, PieceDossier.RECOMMANDATION_1, PieceDossier.RECOMMANDATION_2,
        PieceDossier.LETTRE_ENGAGEMENT, PieceDossier.CV],
    DemandeEtudiant.REINSCRIPTION: PIECES_BASE_MASTER + [PieceDossier.RELEVE_NOTES],
}
OBLIGATOIRES_MASTER = {DemandeEtudiant.INSCRIPTION: PIECES_MASTER[DemandeEtudiant.INSCRIPTION],
                       DemandeEtudiant.REINSCRIPTION: PIECES_BASE_MASTER}
AIDES_MASTER = {
    PieceDossier.ACTE_NAISSANCE: "Copie légalisée de l'extrait de naissance (biométrique pour les nationaux).",
    PieceDossier.DIPLOME_BAC: "Copie certifiée conforme du diplôme ou de l'attestation.",
    PieceDossier.DIPLOME_LICENCE: "Copie certifiée conforme : licence, maîtrise ou diplôme jugé équivalent.",
    PieceDossier.DEMANDE_MANUSCRITE: "Adressée au Directeur Général de l'IST.",
    PieceDossier.PROJET_RECHERCHE: "Une page. Le canevas est à retirer au Service des Études Avancées (SEA).",
    PieceDossier.RECOMMANDATION_1: "Rédigée par un enseignant qui vous connaît.",
    PieceDossier.RECOMMANDATION_2: "Rédigée par un deuxième enseignant qui vous connaît.",
    PieceDossier.LETTRE_ENGAGEMENT: "Lettre personnelle : vous vous engagez à respecter le règlement des études et "
                                    "la charte de formation en master.",
    PieceDossier.CV: "Actualisé.",
}
MASTER = 'master'                       # démarche de l'espace (la demande reste une inscription ou une réinscription)
DEMARCHES = {DemandeEtudiant.INSCRIPTION: 'espace_inscription', DemandeEtudiant.REINSCRIPTION: 'espace_reinscription',
             MASTER: 'espace_master'}
MESSAGE_LIEN = ("Si cette adresse est celle d'un(e) étudiant(e) en Master, un e-mail vient de lui être envoyé avec "
                "un lien personnel (valable 1 heure). Pensez à regarder dans les courriers indésirables.")


def pieces_demandees(type_demande, master=False):
    return (PIECES_MASTER if master else PIECES)[type_demande]


def pieces_obligatoires(type_demande, master=False):
    return (OBLIGATOIRES_MASTER if master else OBLIGATOIRES).get(type_demande, [])


def _piece_deja_deposee(etudiant, type_piece, session):
    """Pièce déjà dans le dossier : le relevé de notes doit être celui de la session, les autres une fois pour toutes."""
    qs = etudiant.pieces.filter(type=type_piece)
    return (qs.filter(session=session) if type_piece == PieceDossier.RELEVE_NOTES else qs).exists()


def _ouvert(session, type_demande):
    if session is None:
        return False
    return session.inscriptions_ouvertes if type_demande == DemandeEtudiant.INSCRIPTION else session.reinscriptions_ouvertes


def _ouvertures(session):
    inscription, reinscription = (_ouvert(session, DemandeEtudiant.INSCRIPTION),
                                  _ouvert(session, DemandeEtudiant.REINSCRIPTION))
    return {DemandeEtudiant.INSCRIPTION: inscription, DemandeEtudiant.REINSCRIPTION: reinscription,
            MASTER: inscription or reinscription}


def _pieces_affichees(types, master=False):
    noms = dict(PieceDossier.TYPES)
    return [(noms[t], AIDES_MASTER.get(t, '') if master else '') for t in types]


@securise
@never_cache
def espace_accueil(request):
    """Page d'accueil : chaque démarche expliquée, un bouton vers la page d'identification de chacune."""
    if request.method == 'POST':                # identification envoyée depuis une ancienne page restée ouverte
        mode = request.POST.get('mode')
        return _demarche(request, mode if mode in DEMARCHES else DemandeEtudiant.INSCRIPTION)
    if request.GET.get('onglet') in DEMARCHES:  # anciens liens
        return redirect(DEMARCHES[request.GET['onglet']])
    session = espace.session_cible()
    ouvertures = _ouvertures(session)
    return render(request, 'pfe/espace/accueil.html', {
        'session': session, 'ouvert_insc': ouvertures[DemandeEtudiant.INSCRIPTION],
        'ouvert_reinsc': ouvertures[DemandeEtudiant.REINSCRIPTION], 'ouvert_master': ouvertures[MASTER],
        'pieces_insc': _pieces_affichees(PIECES[DemandeEtudiant.INSCRIPTION]),
        'pieces_reinsc': _pieces_affichees(PIECES[DemandeEtudiant.REINSCRIPTION]),
        'pieces_master': _pieces_affichees(PIECES_MASTER[DemandeEtudiant.INSCRIPTION], master=True),
        'pieces_master_reinsc': _pieces_affichees(OBLIGATOIRES_MASTER[DemandeEtudiant.REINSCRIPTION], master=True),
    })


@securise
@never_cache
def espace_demarche(request, mode):
    return _demarche(request, mode)


def _demarche(request, mode):
    """Page d'identification d'une démarche (inscription, réinscription ou Master)."""
    session = espace.session_cible()
    ouvert = _ouvertures(session)[mode]
    classe, prefixe = {DemandeEtudiant.INSCRIPTION: (IdentificationInscriptionForm, 'i'),
                       DemandeEtudiant.REINSCRIPTION: (IdentificationReinscriptionForm, 'r'),
                       MASTER: (IdentificationMasterForm, 'm')}[mode]
    form = classe(request.POST if request.method == 'POST' else None, prefix=prefixe)
    erreur = ''
    contexte = {'session': session, 'mode': mode, 'form': form, 'ouvert': ouvert, 'image': f'pfe/img/espace/{mode}.svg'}
    if mode == MASTER and request.method == 'POST':
        attente = espace.bloque(request, request.POST.get('m-email', ''))
        if not ouvert:
            erreur = "Les inscriptions et réinscriptions en ligne sont fermées."
        elif attente:
            erreur = f"Trop de demandes : réessayez dans {attente // 60 + 1} minute(s)."
        elif form.is_valid():
            # Chaque demande de lien compte (pas d'envois en rafale) ; même réponse, l'adresse existe ou non
            espace.echec(request, form.cleaned_data['email'], journal=False)
            _envoyer_lien_master(request, form.cleaned_data['email'], session)
            messages.success(request, MESSAGE_LIEN)
            return redirect('espace_master')
        return render(request, 'pfe/espace/identification.html', {**contexte, 'erreur': erreur})
    if request.method == 'POST' and ouvert:
        ine = request.POST.get(form.add_prefix('ine'), '')
        attente = espace.bloque(request, ine)
        if attente:
            erreur = f"Trop d'essais : réessayez dans {attente // 60 + 1} minute(s)."
        elif form.is_valid():
            d = form.cleaned_data
            try:
                if mode == DemandeEtudiant.INSCRIPTION:
                    etudiant, insc = espace.identifier_inscription(d['ine'], d['pvbac'], session)
                else:
                    etudiant, insc = espace.identifier_reinscription(d['ine'], d['matricule'], session)
            except espace.Refus as e:
                reste = espace.echec(request, d['ine'])
                erreur = str(e) + (f" ({reste} essai{'s' if reste > 1 else ''} restant{'s' if reste > 1 else ''})"
                                   if 0 < reste < espace.ESSAIS_MAX - 1 else '')
            else:
                espace.reussite(request, d['ine'])
                espace.ouvrir_acces(request, etudiant, mode, insc)
                return redirect('espace_dossier')
    return render(request, 'pfe/espace/identification.html', {**contexte, 'erreur': erreur})


def _envoyer_lien_master(request, email, session):
    """
    Adresse d'un étudiant en Master : lien personnel par e-mail. Adresse connue mais accès impossible
    (pas en Master, promotion non prévue, déjà inscrit…) : la raison, par e-mail. Adresse inconnue : rien.
    """
    try:
        etudiant, type_demande, _ = espace.identifier_master(email, session, ouvert=lambda t: _ouvert(session, t))
    except espace.RefusConnu as r:
        _mail_master(request, r.etudiant, session, raison=str(r))
    except espace.Refus:
        return
    else:
        jeton = espace.creer_lien(etudiant)
        _mail_master(request, etudiant, session, lien=reverse('espace_lien', args=[jeton]), type_demande=type_demande)


def _mail_master(request, etudiant, session, lien='', type_demande='', raison=''):
    import logging
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from .comptes import _adresse_site
    site = _adresse_site(request)
    contexte = {'etudiant': etudiant, 'session': session, 'lien': f"{site}{lien}" if lien else '', 'raison': raison,
                'quoi': 'réinscription' if type_demande == DemandeEtudiant.REINSCRIPTION else 'inscription',
                'validite': f"{espace.DUREE_LIEN // 60} minutes", 'site': site,
                'logo': f"{site}{settings.STATIC_URL}pfe/img/logo_ist.png"}
    try:
        message = EmailMultiAlternatives(
            "gePed : votre lien d'inscription en Master" if lien else "gePed : inscription en Master",
            render_to_string('pfe/emails/lien_master.txt', contexte), settings.DEFAULT_FROM_EMAIL, [etudiant.email])
        message.attach_alternative(render_to_string('pfe/emails/lien_master.html', contexte), 'text/html')
        message.send()
    except Exception:
        logging.getLogger('pfe').exception("Espace étudiant : envoi du lien Master impossible (étudiant %s)",
                                           etudiant.pk)


@securise
@never_cache
def espace_lien(request, jeton):
    """
    Lien personnel reçu par e-mail. Il n'est utilisé qu'au clic sur « Ouvrir mon dossier » (POST) : une
    messagerie qui ouvre les liens pour les vérifier ne le consomme pas.
    """
    session = espace.session_cible()
    etudiant = espace.lire_lien(jeton, consommer=request.method == 'POST')
    if etudiant is None:
        messages.error(request, "Ce lien a expiré ou a déjà servi : saisissez de nouveau votre adresse e-mail pour "
                                "en recevoir un autre.")
        return redirect('espace_master')
    if request.method == 'POST':
        try:
            etudiant, type_demande, insc = espace.identifier_master(etudiant.email, session,
                                                                     ouvert=lambda t: _ouvert(session, t))
        except espace.Refus as r:
            messages.error(request, str(r))
            return redirect('espace_master')
        espace.ouvrir_acces(request, etudiant, type_demande, insc, master=True)
        return redirect('espace_dossier')
    return render(request, 'pfe/espace/lien.html', {'etudiant': etudiant, 'session': session})


@securise
@never_cache
def espace_dossier(request):
    a = espace.acces(request)
    session = espace.session_cible()
    if a is None or not _ouvert(session, a['type']):
        messages.info(request, "Votre accès a expiré : identifiez-vous de nouveau.")
        return redirect('espace_accueil')
    # Envoi trop lourd : refusé AVANT de lire le contenu (rien n'est décodé ni enregistré)
    try:
        taille_envoi = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        taille_envoi = 0
    if request.method == 'POST' and taille_envoi > ENVOI_MAX:
        messages.error(request, "Envoi trop lourd : envoyez moins de pages, ou des PDF plus légers.")
        return redirect('espace_dossier')
    etudiant = Etudiant.objects.get(pk=a['etudiant'])
    insc = Inscription.objects.select_related('session', 'licence', 'filiere', 'promotion').get(pk=a['inscription'])
    type_demande = a['type']
    master = bool(a.get('master'))
    reinscription = type_demande == DemandeEtudiant.REINSCRIPTION
    demande = DemandeEtudiant.objects.filter(etudiant=etudiant, session=session, type=type_demande).first()
    if demande and demande.statut == DemandeEtudiant.VALIDEE:
        espace.fermer_acces(request)
        messages.success(request, f"Votre {demande.get_type_display().lower()} pour {session} est déjà validée.")
        return redirect('espace_accueil')
    niveaux, suivant = espace.niveaux_proposes(insc, session) if reinscription else ([], None)

    # Contacts VIDES : l'étudiant les saisit lui-même (à jour) ; ceux d'une demande déjà envoyée sont repris
    initial = {}
    if reinscription:                       # Boursier et formation initiale cochés par défaut
        initial.update(licence=suivant.pk if suivant else None, boursier='boursier', type_formation='initiale')
    if master and not demande:              # identifié(e) par son adresse : déjà remplie
        initial['email'] = etudiant.email
    if demande:
        initial.update({c: v for c, v in (demande.contacts or {}).items() if v})
        if reinscription:
            initial.update({k: v for k, v in (('licence', demande.licence_id), ('boursier', demande.boursier),
                                               ('type_formation', demande.type_formation)) if v})
    form = DossierEtudiantForm(request.POST or None, request.FILES or None, initial=initial,
                               reinscription=reinscription, niveaux=niveaux, etudiant=etudiant)
    erreurs_pieces = {}
    # Photo d'identité OBLIGATOIRE : une nouvelle, sinon celle déjà envoyée avec la demande ou celle de la fiche
    a_deja_photo = bool(demande and demande.photo) or bool(url_photo(etudiant))
    if request.method == 'POST' and form.is_valid() and not form.cleaned_data.get('photo') and not a_deja_photo:
        form.add_error('photo', "Ajoutez votre photo d'identité : elle est obligatoire.")
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        envois = {t: request.FILES.getlist(f'piece_{t}') for t in pieces_demandees(type_demande, master)}
        # Les pièces sont préparées (et contrôlées) avant d'enregistrer quoi que ce soit
        preparees = {}
        for t, fichiers in envois.items():
            if fichiers:
                try:
                    dossier.preparer(fichiers)
                    preparees[t] = fichiers
                except ValidationError as e:
                    erreurs_pieces[t] = ' '.join(e.messages)
        # Pièces obligatoires : envoyées maintenant, ou déjà dans le dossier
        for t in pieces_obligatoires(type_demande, master):
            if t not in preparees and t not in erreurs_pieces and not _piece_deja_deposee(etudiant, t, session):
                erreurs_pieces[t] = "Obligatoire : scannez ou envoyez cette pièce."
        if not erreurs_pieces:
            with transaction.atomic():
                if demande is None:
                    demande = DemandeEtudiant(etudiant=etudiant, session=session, type=type_demande)
                demande.statut = DemandeEtudiant.EN_ATTENTE
                demande.contacts = {c: d.get(c) or '' for c in DemandeEtudiant.CHAMPS_CONTACTS}
                demande.derniere_inscription = insc if reinscription else None
                if reinscription:
                    demande.licence, demande.boursier, demande.type_formation = (
                        d['licence'], d['boursier'], d['type_formation'])
                else:                       # inscription : boursier et formation initiale par défaut
                    demande.boursier = demande.boursier or 'boursier'
                    demande.type_formation = demande.type_formation or 'initiale'
                if d.get('photo'):
                    from django.core.files.base import ContentFile
                    from django.utils import timezone
                    from .models import stockage_prive
                    if demande.photo:
                        stockage_prive().delete(demande.photo.name)
                    demande.photo = stockage_prive().save(
                        f"demandes/photos/{timezone.now():%Y}/{etudiant.matricule or etudiant.pk}.jpg",
                        ContentFile(d['photo']))
                demande.save()
                for t, fichiers in preparees.items():
                    for f in fichiers:
                        f.seek(0)
                    dossier.enregistrer(etudiant, t, fichiers, par_etudiant=True,
                                        session=session if t == PieceDossier.RELEVE_NOTES else None)
                _signer(request, demande, d['signature'], master)
            return redirect('espace_confirmation')

    # Pièces déjà dans le dossier (déposées par l'étudiant ou par la scolarité) : affichées « déposée »
    deposees = {p.type: p for p in etudiant.pieces.filter(type__in=pieces_demandees(type_demande, master))
                .order_by('date')}
    if PieceDossier.RELEVE_NOTES in deposees and deposees[PieceDossier.RELEVE_NOTES].session_id != session.pk:
        del deposees[PieceDossier.RELEVE_NOTES]           # relevé d'une autre année : à redéposer
    # Demande déjà envoyée (en attente ou refusée) : ce qui est fourni et ce qui reste à fournir
    recapitulatif = []
    if demande:
        contacts = demande.contacts or {}
        recapitulatif.append(('Coordonnées', all(contacts.get(c) for c in DemandeEtudiant.CHAMPS_CONTACTS)))
        if reinscription:
            recapitulatif.append((f"Niveau demandé : {demande.licence}" if demande.licence else 'Niveau demandé',
                                  bool(demande.licence_id)))
        recapitulatif.append(("Photo d'identité", bool(demande.photo) or bool(url_photo(etudiant))))
        recapitulatif += [(dict(PieceDossier.TYPES)[t], t in deposees) for t in pieces_demandees(type_demande, master)
                          if t in deposees or t in pieces_obligatoires(type_demande, master)]
        recapitulatif.append(('Signature', bool(demande.signature)))
    return render(request, 'pfe/espace/dossier.html', {
        'recapitulatif': recapitulatif,
        'signature_url': (f"{reverse('espace_signature')}?v={int(demande.signee_le.timestamp())}"
                          if demande and demande.signature and demande.signee_le else None),
        'session': session, 'etudiant': etudiant, 'insc': insc, 'form': form, 'demande': demande,
        'reinscription': reinscription, 'master': master,
        # Photo visible par l'étudiant : la nouvelle en attente, sinon l'actuelle (servie par espace_photo)
        'photo_url': (f"{reverse('espace_photo')}?v={int(demande.date_maj.timestamp()) if demande else 0}"
                      if (demande and demande.photo) or url_photo(etudiant) else None),
        'photo_en_attente': bool(demande and demande.photo),
        'photo_requise': not ((demande and demande.photo) or url_photo(etudiant)),
        'departement': _nom_departement(insc.filiere) if insc.filiere else '',
        'programme': insc.filiere.nom if insc.filiere else '',        # nom réel : la description porte souvent le département
        'pieces': [(t, dict(PieceDossier.TYPES)[t], deposees.get(t), erreurs_pieces.get(t),
                    t in pieces_obligatoires(type_demande, master), AIDES_MASTER.get(t, '') if master else '')
                   for t in pieces_demandees(type_demande, master)],
        'toutes_obligatoires': set(pieces_demandees(type_demande, master)) == set(pieces_obligatoires(type_demande, master)),
        'expire_minutes': max(1, int((a['expire'] - __import__('time').time()) // 60)),
    })


def _empreinte(demande, master=False):
    """SHA-256 de ce que l'étudiant a signé : identité, demande, coordonnées, photo et pièces du dossier."""
    import hashlib
    import json
    e = demande.etudiant
    pieces = sorted((p.type, p.fichier.name, p.taille)
                    for p in e.pieces.filter(type__in=pieces_demandees(demande.type, master)))
    contenu = {
        'etudiant': [e.pk, e.matricule, e.ine, e.prenom, e.nom], 'demande': [demande.type, demande.session_id],
        'contacts': demande.contacts, 'niveau': demande.licence_id, 'bourse': demande.boursier,
        'formation': demande.type_formation, 'photo': demande.photo.name if demande.photo else '', 'pieces': pieces,
        'signee_le': demande.signee_le.isoformat(),
    }
    return hashlib.sha256(json.dumps(contenu, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _signer(request, demande, png, master=False):
    """Enregistre la signature (image privée), la date, l'appareil et l'empreinte des informations signées."""
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from .models import stockage_prive
    stockage = stockage_prive()
    if demande.signature:
        stockage.delete(demande.signature.name)
    demande.signature = stockage.save(
        f"demandes/signatures/{timezone.now():%Y}/{demande.etudiant.matricule or demande.etudiant_id}_{demande.type}.png",
        ContentFile(png))
    demande.signee_le = timezone.now()
    demande.signature_ip = (request.META.get('REMOTE_ADDR') or '')[:45]
    demande.signature_appareil = (request.META.get('HTTP_USER_AGENT') or '')[:255]
    demande.empreinte = _empreinte(demande, master)
    demande.save(update_fields=['signature', 'signee_le', 'signature_ip', 'signature_appareil', 'empreinte',
                                'date_maj'])


@securise
@never_cache
def espace_confirmation(request):
    a = espace.acces(request)
    if a is None:
        return redirect('espace_accueil')
    session = espace.session_cible()
    demande = DemandeEtudiant.objects.filter(etudiant_id=a['etudiant'], session=session, type=a['type']).first()
    if demande is None:
        return redirect('espace_dossier')
    return render(request, 'pfe/espace/confirmation.html', {'demande': demande, 'session': session})


@securise
@never_cache
def espace_photo(request):
    """
    Photo de l'étudiant identifié (les photos ne sont servies qu'aux comptes connectés : l'espace
    n'a pas de compte). La nouvelle photo en attente de validation s'il en a envoyé une, sinon l'actuelle.
    """
    from django.http import FileResponse, Http404
    from .models import stockage_prive
    from .photos import a_une_photo
    a = espace.acces(request)
    if a is None:
        raise Http404
    demande = (DemandeEtudiant.objects.filter(etudiant_id=a['etudiant'], session=espace.session_cible(), type=a['type'])
               .exclude(photo='').first())
    if demande:
        fichier = stockage_prive().open(demande.photo.name, 'rb')
    else:
        etudiant = Etudiant.objects.get(pk=a['etudiant'])
        if not a_une_photo(etudiant):
            raise Http404
        fichier = etudiant.photo.open('rb')
    reponse = FileResponse(fichier, content_type='image/jpeg')
    reponse['Cache-Control'] = 'private, no-store'
    reponse['X-Content-Type-Options'] = 'nosniff'
    return reponse


@securise
@never_cache
def espace_signature(request):
    """Signature déjà enregistrée avec la demande de l'étudiant identifié (sinon 404)."""
    from django.http import FileResponse, Http404
    from .models import stockage_prive
    a = espace.acces(request)
    if a is None:
        raise Http404
    demande = (DemandeEtudiant.objects.filter(etudiant_id=a['etudiant'], session=espace.session_cible(), type=a['type'])
               .exclude(signature='').first())
    if demande is None:
        raise Http404
    reponse = FileResponse(stockage_prive().open(demande.signature.name, 'rb'), content_type='image/png')
    reponse['Cache-Control'] = 'private, no-store'
    return reponse


@securise
@require_POST
def espace_quitter(request):
    espace.fermer_acces(request)
    messages.info(request, "Vous avez quitté votre dossier.")
    return redirect('espace_accueil')
