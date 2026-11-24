"""
Exclusions d'un programme : liste des exclusions et des propositions (dettes), exclusion depuis la fiche ou la
liste (scolarité), réintégration (chef de la scolarité, administrateur). Règles : pfe/exclusions.py.
Les autres espaces consultent, chacun pour les étudiants qu'il voit.
"""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import exclusions
from .models import Exclusion, Session
from .roles import (exclusions_gestion_requise, exclusions_lecture_requise, get_profil, inscriptions_visibles,
                    reintegration_requise)


def _visibles(profil):
    return Exclusion.objects.filter(inscription__in=inscriptions_visibles(profil))


def _retour(request, defaut):
    suivant = request.POST.get('suivant', '')
    if suivant.startswith('/') and not suivant.startswith('//'):
        return redirect(suivant)
    return redirect(defaut)


@login_required
@exclusions_lecture_requise
def exclusions_liste(request):
    """Exclusions (en vigueur ou levées par réintégration) et, pour la scolarité, les propositions de GePed."""
    profil = get_profil(request)
    g = request.GET
    q = (g.get('q') or '').strip()
    etat = g.get('etat') if g.get('etat') in ('en_vigueur', 'reintegres') else ''
    motif = g.get('motif') if g.get('motif') in dict(Exclusion.MOTIFS) else ''
    sessions = Session.objects.order_by('-annee')
    session_id = int(g['session']) if (g.get('session') or '').isdigit() else None
    qs = _visibles(profil).select_related('etudiant', 'filiere', 'inscription__session', 'inscription__licence',
                                          'inscription__promotion', 'prononcee_par', 'reintegre_par')
    if session_id:
        qs = qs.filter(inscription__session_id=session_id)
    if etat == 'en_vigueur':
        qs = qs.filter(reintegre_le__isnull=True)
    elif etat == 'reintegres':
        qs = qs.filter(reintegre_le__isnull=False)
    if motif:
        qs = qs.filter(motif=motif)
    for terme in q.split()[:4]:
        qs = qs.filter(Q(etudiant__matricule__icontains=terme) | Q(etudiant__nom__icontains=terme)
                       | Q(etudiant__prenom__icontains=terme) | Q(etudiant__ine__icontains=terme))
    if g.get('imprimer') == 'pdf':
        from .pdf_listes import imprimer_liste
        filtres = [('Session', next((s.annee for s in sessions if s.pk == session_id), None)),
                   ('État', {'en_vigueur': 'En vigueur', 'reintegres': 'Réintégrés'}.get(etat)),
                   ('Motif', dict(Exclusion.MOTIFS).get(motif)), ('Recherche', q)]
        lignes = [{'matricule': x.etudiant.matricule, 'ine': x.etudiant.ine, 'prenom': x.etudiant.prenom,
                   'nom': x.etudiant.nom, 'telephone': x.etudiant.telephone,
                   'niveau': x.inscription.licence.nom if x.inscription.licence else '',
                   'promotion': x.inscription.promotion.nom if x.inscription.promotion else '',
                   'programme': x.filiere.nom, 'session': '' if session_id else x.inscription.session.annee}
                  for x in qs]
        return imprimer_liste(request, "ÉTUDIANTS EXCLUS D'UN PROGRAMME", filtres, lignes, 'exclusions')
    page = Paginator(qs, 25).get_page(g.get('page'))
    return render(request, 'pfe/exclusions_liste.html', {
        'page': page, 'q': q, 'etat': etat, 'motif': motif, 'motifs': Exclusion.MOTIFS, 'sessions': sessions,
        'session_id': session_id, 'en_vigueur': _visibles(profil).filter(reintegre_le__isnull=True).count(),
        'propositions': exclusions.propositions(inscriptions_visibles(profil)),
        'seuils': exclusions.seuils_exclusion(), 'aujourdhui': datetime.date.today(), 'actif': 'exclusions',
    })


@login_required
@exclusions_gestion_requise
@require_POST
def exclusion_enregistrer(request, pk):
    """Exclut du programme l'étudiant de l'inscription `pk` (motif, précisions, date)."""
    inscription = get_object_or_404(inscriptions_visibles(get_profil(request)).select_related('etudiant', 'filiere'),
                                    pk=pk)
    try:
        date = datetime.date.fromisoformat((request.POST.get('date') or '').strip()) if request.POST.get('date') \
            else None
    except ValueError:
        date = None
    try:
        x = exclusions.exclure(inscription, request.POST.get('motif', ''), request.POST.get('details', ''), date,
                               request.user)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        e = inscription.etudiant
        messages.success(request, f"{e.prenom} {e.nom} est exclu(e) du programme {inscription.filiere} "
                                  f"({x.get_motif_display().lower()}).")
    return _retour(request, reverse("etudiant_fiche", args=[inscription.pk]) + "#exclusion")


@login_required
@reintegration_requise
@require_POST
def exclusion_reintegrer(request, pk):
    x = get_object_or_404(_visibles(get_profil(request)).select_related('etudiant', 'filiere'), pk=pk)
    try:
        exclusions.reintegrer(x, request.POST.get('motif_reintegration', ''), request.user)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, f"{x.etudiant.prenom} {x.etudiant.nom} est réintégré(e) dans le programme "
                                  f"{x.filiere}.")
    return _retour(request, reverse("etudiant_fiche", args=[x.inscription_id]) + "#exclusion")
