"""
Espace étudiant (public, sans compte) : /etudiant/
  - Inscription   : préinscrit, INE + PV du bac → contacts, photo, pièces (facultatives).
  - Réinscription : ancien étudiant, INE + matricule → contacts, niveau, bourse, formation,
                    relevé de notes de la dernière année.
Rien n'est appliqué à la fiche avant la validation par la scolarité (voir pfe/espace_etudiant.py).
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import espace_etudiant as espace
from . import pieces as dossier
from .attestations import _nom_departement, _nom_programme
from .forms import DossierEtudiantForm, IdentificationInscriptionForm, IdentificationReinscriptionForm
from .models import DemandeEtudiant, Etudiant, Inscription, PieceDossier
from .photos import url_photo

PIECES = {
    DemandeEtudiant.INSCRIPTION: [PieceDossier.ACTE_NAISSANCE, PieceDossier.DIPLOME_BAC, PieceDossier.RELEVE_BAC],
    DemandeEtudiant.REINSCRIPTION: [PieceDossier.RELEVE_NOTES],
}


def _ouvert(session, type_demande):
    if session is None:
        return False
    return session.inscriptions_ouvertes if type_demande == DemandeEtudiant.INSCRIPTION else session.reinscriptions_ouvertes


@never_cache
def espace_accueil(request):
    session = espace.session_cible()
    mode = request.POST.get('mode') or request.GET.get('onglet') or DemandeEtudiant.INSCRIPTION
    if mode not in (DemandeEtudiant.INSCRIPTION, DemandeEtudiant.REINSCRIPTION):
        mode = DemandeEtudiant.INSCRIPTION
    form_insc = IdentificationInscriptionForm(request.POST if mode == DemandeEtudiant.INSCRIPTION and request.method == 'POST' else None, prefix='i')
    form_reinsc = IdentificationReinscriptionForm(request.POST if mode == DemandeEtudiant.REINSCRIPTION and request.method == 'POST' else None, prefix='r')
    erreur = ''
    attente = espace.bloque(request)
    if request.method == 'POST' and _ouvert(session, mode):
        form = form_insc if mode == DemandeEtudiant.INSCRIPTION else form_reinsc
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
                reste = espace.echec(request)
                erreur = str(e) + (f" ({reste} essai{'s' if reste > 1 else ''} restant{'s' if reste > 1 else ''})"
                                   if 0 < reste < espace.ESSAIS_MAX - 1 else '')
            else:
                espace.reussite(request)
                espace.ouvrir_acces(request, etudiant, mode, insc)
                return redirect('espace_dossier')
    return render(request, 'pfe/espace/accueil.html', {
        'session': session, 'mode': mode, 'form_insc': form_insc, 'form_reinsc': form_reinsc, 'erreur': erreur,
        'ouvert_insc': _ouvert(session, DemandeEtudiant.INSCRIPTION),
        'ouvert_reinsc': _ouvert(session, DemandeEtudiant.REINSCRIPTION),
    })


@never_cache
def espace_dossier(request):
    a = espace.acces(request)
    session = espace.session_cible()
    if a is None or not _ouvert(session, a['type']):
        messages.info(request, "Votre accès a expiré : identifiez-vous de nouveau.")
        return redirect('espace_accueil')
    etudiant = Etudiant.objects.get(pk=a['etudiant'])
    insc = Inscription.objects.select_related('session', 'licence', 'filiere', 'promotion').get(pk=a['inscription'])
    type_demande = a['type']
    reinscription = type_demande == DemandeEtudiant.REINSCRIPTION
    demande = DemandeEtudiant.objects.filter(etudiant=etudiant, session=session, type=type_demande).first()
    if demande and demande.statut == DemandeEtudiant.VALIDEE:
        espace.fermer_acces(request)
        messages.success(request, f"Votre {demande.get_type_display().lower()} pour {session} est déjà validée.")
        return redirect('espace_accueil')
    niveaux, suivant = espace.niveaux_proposes(insc) if reinscription else ([], None)

    initial = {c: getattr(etudiant, c) or '' for c in DemandeEtudiant.CHAMPS_CONTACTS}
    if reinscription:                       # Boursier et formation initiale cochés par défaut
        initial.update(licence=suivant.pk if suivant else None, boursier='boursier', type_formation='initiale')
    if demande:
        initial.update({c: v for c, v in (demande.contacts or {}).items() if v})
        if reinscription:
            initial.update({k: v for k, v in (('licence', demande.licence_id), ('boursier', demande.boursier),
                                               ('type_formation', demande.type_formation)) if v})
    form = DossierEtudiantForm(request.POST or None, request.FILES or None, initial=initial,
                               reinscription=reinscription, niveaux=niveaux, etudiant=etudiant)
    erreurs_pieces = {}
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        envois = {t: request.FILES.getlist(f'piece_{t}') for t in PIECES[type_demande]}
        # Les pièces sont préparées (et contrôlées) avant d'enregistrer quoi que ce soit
        preparees = {}
        for t, fichiers in envois.items():
            if fichiers:
                try:
                    dossier.preparer(fichiers)
                    preparees[t] = fichiers
                except ValidationError as e:
                    erreurs_pieces[t] = ' '.join(e.messages)
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
            return redirect('espace_confirmation')

    deposees = {p.type: p for p in etudiant.pieces.filter(type__in=PIECES[type_demande], par_etudiant=True)}
    return render(request, 'pfe/espace/dossier.html', {
        'session': session, 'etudiant': etudiant, 'insc': insc, 'form': form, 'demande': demande,
        'reinscription': reinscription, 'photo_url': url_photo(etudiant),
        'departement': _nom_departement(insc.filiere) if insc.filiere else '',
        'programme': _nom_programme(insc.filiere) if insc.filiere else '',
        'pieces': [(t, dict(PieceDossier.TYPES)[t], deposees.get(t), erreurs_pieces.get(t)) for t in PIECES[type_demande]],
        'expire_minutes': max(1, int((a['expire'] - __import__('time').time()) // 60)),
    })


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


@require_POST
def espace_quitter(request):
    espace.fermer_acces(request)
    messages.info(request, "Vous avez quitté votre dossier.")
    return redirect('espace_accueil')
