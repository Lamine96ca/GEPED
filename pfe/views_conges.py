"""
Congés académiques : accordés, modifiés, levés ou annulés depuis la fiche de l'étudiant (chef de la
scolarité, super utilisateur) ; liste des congés (scolarité, directeurs de Master pour leurs étudiants).
Règles : pfe/conges.py.
"""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import conges
from .models import CongeAcademique, Session
from .roles import conges_requis, etudiants_requise, get_profil, inscriptions_gerees, lecture_conges_requise


def _date(valeur):
    try:
        return datetime.date.fromisoformat((valeur or '').strip()) if (valeur or '').strip() else None
    except ValueError:
        return None


def _conges_geres(request):
    return CongeAcademique.objects.filter(inscription__in=inscriptions_gerees(get_profil(request)))


def _conges_visibles(request):
    """Liste : ceux que l'on gère ; la Direction Générale des Études voit tous les congés (sans les modifier)."""
    profil = get_profil(request)
    if profil.est_dge and not profil.est_scolarite:
        return CongeAcademique.objects.all()
    return _conges_geres(request)


def _retour(request, conge_ou_inscription_pk):
    suivant = request.POST.get('suivant', '')
    if suivant.startswith('/') and not suivant.startswith('//'):
        return redirect(suivant)
    return redirect('etudiant_fiche', pk=conge_ou_inscription_pk)


@login_required
@conges_requis
@require_POST
def conge_enregistrer(request, pk):
    """Accorde le congé de l'inscription `pk`, ou modifie celui qu'elle a déjà."""
    inscription = get_object_or_404(inscriptions_gerees(get_profil(request)).select_related('etudiant', 'session'),
                                    pk=pk)
    conge = CongeAcademique.objects.filter(inscription=inscription).first()
    debut, fin = _date(request.POST.get('date_debut')), _date(request.POST.get('date_fin'))
    try:
        conge = conges.enregistrer(inscription, request.POST.get('motif', ''), debut, fin,
                                   request.FILES.getlist('justificatif'), request.user, conge=conge)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        e = inscription.etudiant
        messages.success(request, f"Congé académique de {e.prenom} {e.nom} enregistré pour {inscription.session} : "
                                  f"l'inscription est suspendue.")
    return _retour(request, inscription.pk)


@login_required
@conges_requis
@require_POST
def conge_lever(request, pk):
    conge = get_object_or_404(_conges_geres(request).select_related('inscription__etudiant'), pk=pk)
    if conge.en_cours:
        conges.lever(conge, request.user)
        e = conge.inscription.etudiant
        messages.success(request, f"Congé levé : {e.prenom} {e.nom} peut de nouveau s'inscrire, au même niveau "
                                  f"({conge.inscription.licence}).")
    return _retour(request, conge.inscription_id)


@login_required
@conges_requis
@require_POST
def conge_annuler(request, pk):
    """Congé saisi par erreur : supprimé, l'inscription redevient normale."""
    conge = get_object_or_404(_conges_geres(request).select_related('inscription__etudiant'), pk=pk)
    inscription_pk, e = conge.inscription_id, conge.inscription.etudiant
    conges.annuler(conge)
    messages.success(request, f"Congé de {e.prenom} {e.nom} annulé : l'inscription n'est plus suspendue.")
    return _retour(request, inscription_pk)


@login_required
@etudiants_requise
def conge_justificatif(request, pk):
    conge = get_object_or_404(_conges_geres(request).select_related('inscription__etudiant'), pk=pk)
    if not conge.justificatif:
        raise Http404
    e = conge.inscription.etudiant
    nom = f"conge_{e.matricule or e.pk}.pdf"
    reponse = FileResponse(conges.ouvrir_justificatif(conge), content_type='application/pdf', filename=nom)
    reponse['Content-Disposition'] = f'inline; filename="{nom}"'
    reponse['X-Content-Type-Options'] = 'nosniff'
    reponse['Cache-Control'] = 'private, no-store'
    return reponse


@login_required
@lecture_conges_requise
def conges_liste(request):
    """Congés académiques : par session et par état, avec recherche ; 25 par page."""
    g = request.GET
    q = (g.get('q') or '').strip()
    etat = g.get('etat') if g.get('etat') in ('en_cours', 'leves') else ''
    sessions = Session.objects.order_by('-annee')
    session_id = int(g['session']) if (g.get('session') or '').isdigit() else None
    qs = _conges_visibles(request).select_related('inscription__etudiant', 'inscription__session',
                                               'inscription__licence', 'inscription__filiere', 'accorde_par')
    if session_id:
        qs = qs.filter(inscription__session_id=session_id)
    if etat == 'en_cours':
        qs = qs.filter(levee_le__isnull=True)
    elif etat == 'leves':
        qs = qs.filter(levee_le__isnull=False)
    for terme in q.split()[:4]:
        qs = qs.filter(Q(inscription__etudiant__matricule__icontains=terme)
                       | Q(inscription__etudiant__nom__icontains=terme)
                       | Q(inscription__etudiant__prenom__icontains=terme)
                       | Q(inscription__etudiant__ine__icontains=terme))
    page = Paginator(qs, 25).get_page(g.get('page'))
    return render(request, 'pfe/conges_liste.html', {
        'page': page, 'q': q, 'etat': etat, 'sessions': sessions, 'session_id': session_id,
        'en_cours': _conges_visibles(request).filter(levee_le__isnull=True).count(), 'actif': 'conges',
    })
