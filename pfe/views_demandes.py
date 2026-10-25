"""
Demandes déposées dans l'espace étudiant (scolarité) : « Demandes en ligne ».
  - ouverture / fermeture des inscriptions et des réinscriptions en ligne pour la session ;
  - liste des demandes (réinscriptions, inscriptions), par statut ;
  - fiche d'une demande : informations, changements proposés (ancien → nouveau), pièces et, pour une
    réinscription, TOUTES les notes de l'étudiant empilées par année (notes < 5 sur fond rouge clair),
    puis « Valider » (applique la demande, crée l'inscription) ou « Refuser » (avec motif).
Le choix revient à l'agent : rien n'est validé automatiquement.
"""
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import espace_etudiant as espace
from .attestations import _nom_departement, _nom_programme
from .models import DemandeEtudiant, Matiere, Note, Resultat, Session
from .roles import scolarite_requise

LIBELLES_CONTACTS = {'telephone': 'Téléphone', 'email': 'E-mail', 'adresse': 'Adresse', 'ville': 'Ville / Commune',
                     'tuteur': 'Tuteur', 'contact_de_tuteur': 'Contact du tuteur'}
SEUIL_ROUGE = 5


@login_required
@scolarite_requise
def demandes_liste(request):
    session = espace.session_cible()
    if request.method == 'POST' and session:
        champ = request.POST.get('ouverture')
        if champ in ('inscriptions_ouvertes', 'reinscriptions_ouvertes'):
            setattr(session, champ, request.POST.get('valeur') == '1')
            session.save(update_fields=[champ])
            quoi = 'inscriptions' if champ == 'inscriptions_ouvertes' else 'réinscriptions'
            messages.success(request, f"Les {quoi} en ligne sont {'ouvertes' if getattr(session, champ) else 'fermées'} "
                                      f"pour {session}.")
        return redirect(request.get_full_path())
    type_demande = request.GET.get('type') if request.GET.get('type') in dict(DemandeEtudiant.TYPES) \
        else DemandeEtudiant.REINSCRIPTION
    statut = request.GET.get('statut') if request.GET.get('statut') in dict(DemandeEtudiant.STATUTS) \
        else DemandeEtudiant.EN_ATTENTE
    base = DemandeEtudiant.objects.filter(session=session)
    compteurs = {(c['type'], c['statut']): c['n'] for c in base.values('type', 'statut').annotate(n=Count('id'))}
    demandes = (base.filter(type=type_demande, statut=statut)
                .select_related('etudiant', 'licence', 'derniere_inscription__licence', 'derniere_inscription__filiere')
                .order_by('date_depot'))
    q = (request.GET.get('q') or '').strip()
    if q:
        demandes = demandes.filter(Q(etudiant__matricule__icontains=q) | Q(etudiant__nom__icontains=q)
                                   | Q(etudiant__prenom__icontains=q) | Q(etudiant__ine__icontains=q))
    return render(request, 'pfe/demandes_liste.html', {
        'session': session, 'type_demande': type_demande, 'statut': statut, 'demandes': demandes[:300], 'q': q,
        'compteurs': compteurs, 'types': DemandeEtudiant.TYPES, 'statuts': DemandeEtudiant.STATUTS,
        'url_espace': request.build_absolute_uri('/etudiant/'), 'actif': 'demandes',
    })


def notes_empilees(etudiant):
    """
    Toutes les notes de l'étudiant, année par année (ordre des sessions), semestre par semestre ;
    une matière du programme sans note apparaît vide. Avec le résultat annuel calculé.
    """
    inscriptions = list(etudiant.etudiant_inscription.filter(filiere__isnull=False)
                        .select_related('session', 'licence', 'filiere').order_by('session__annee', 'licence__nom'))
    notes = {n.matiere_id: n for n in Note.objects.filter(etudiant=etudiant)}
    resultats = {(r.session_id, r.licence_id): r for r in Resultat.objects.filter(etudiant=etudiant,
                                                                                    niveau=Resultat.ANNEE)}
    annees = []
    for insc in inscriptions:
        matieres = (Matiere.objects.filter(filiere=insc.filiere, session=insc.session, licence=insc.licence)
                    .select_related('semestre').order_by('semestre__nom', 'nom'))
        semestres = defaultdict(list)
        for m in matieres:
            n = notes.get(m.pk)
            finale = n.moyenne_finale if n else None
            semestres[m.semestre.nom if m.semestre else '—'].append({
                'matiere': m, 'note': n, 'finale': finale, 'rouge': finale is not None and finale < SEUIL_ROUGE,
                'manquante': finale is None})
        annees.append({'inscription': insc, 'semestres': sorted(semestres.items()),
                       'resultat': resultats.get((insc.session_id, insc.licence_id)),
                       'nb_rouges': sum(1 for lst in semestres.values() for x in lst if x['rouge']),
                       'nb_manquantes': sum(1 for lst in semestres.values() for x in lst if x['manquante'])})
    return annees


@login_required
@scolarite_requise
def demande_detail(request, pk):
    demande = get_object_or_404(DemandeEtudiant.objects.select_related(
        'etudiant', 'session', 'licence', 'derniere_inscription__licence', 'derniere_inscription__filiere',
        'derniere_inscription__promotion', 'derniere_inscription__session', 'traitee_par'), pk=pk)
    e = demande.etudiant
    if request.method == 'POST' and demande.statut == DemandeEtudiant.EN_ATTENTE:
        action = request.POST.get('action')
        if action == 'valider':
            espace.appliquer(demande, request.user)
            if demande.type == DemandeEtudiant.REINSCRIPTION:
                messages.success(request, f"Réinscription validée : {e.prenom} {e.nom} est inscrit(e) en "
                                          f"{demande.licence} pour {demande.session}.")
            else:
                messages.success(request, f"Inscription validée : {e.prenom} {e.nom} n'est plus préinscrit(e).")
            return redirect(f"{request.path}")
        if action == 'refuser':
            motif = (request.POST.get('motif') or '').strip()
            if not motif:
                messages.error(request, "Indiquez le motif du refus : il sera affiché à l'étudiant.")
            else:
                espace.refuser(demande, request.user, motif)
                messages.success(request, "Demande refusée. L'étudiant verra le motif et pourra la corriger.")
                return redirect(f"{request.path}")
    changements = []
    for champ, libelle in LIBELLES_CONTACTS.items():
        nouveau = (demande.contacts or {}).get(champ) or ''
        ancien = getattr(e, champ) or ''
        if nouveau and nouveau != ancien:
            changements.append((libelle, ancien, nouveau))
    for champ, libelle in (('boursier', 'Bourse'), ('type_formation', 'Type de formation')):
        nouveau = getattr(demande, champ)
        if nouveau and nouveau != getattr(e, champ):
            changements.append((libelle, getattr(e, f'get_{champ}_display')() or '—',
                                getattr(demande, f'get_{champ}_display')()))
    d = demande.derniere_inscription
    insc_session = e.etudiant_inscription.filter(session=demande.session).select_related('licence', 'filiere').first()
    return render(request, 'pfe/demande_detail.html', {
        'demande': demande, 'etudiant': e, 'changements': changements,
        'derniere': d, 'insc_session': insc_session,
        'departement': _nom_departement(d.filiere) if d and d.filiere else '',
        'programme': _nom_programme(d.filiere) if d and d.filiere else '',
        'pieces': e.pieces.select_related('session'),
        'annees': notes_empilees(e) if demande.type == DemandeEtudiant.REINSCRIPTION else None,
        'seuil_rouge': SEUIL_ROUGE, 'actif': 'demandes',
        'fiche': (insc_session or d or e.etudiant_inscription.order_by('-session__annee').first()),
    })


@login_required
@scolarite_requise
def demande_photo(request, pk):
    """Nouvelle photo proposée par l'étudiant (en attente)."""
    from .models import stockage_prive
    demande = get_object_or_404(DemandeEtudiant, pk=pk)
    if not demande.photo:
        raise Http404
    reponse = FileResponse(stockage_prive().open(demande.photo.name, 'rb'), content_type='image/jpeg')
    reponse['Cache-Control'] = 'private, no-store'
    return reponse


def nb_demandes_en_attente():
    session = espace.session_cible()
    return DemandeEtudiant.objects.filter(session=session, statut=DemandeEtudiant.EN_ATTENTE).count() if session else 0
