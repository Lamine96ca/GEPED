"""
Relevés de notes (scolarité) : on cherche l'étudiant, on choisit dans son parcours le relevé
voulu (semestre, année, extrait par programme). Un relevé déjà délivré pour le même objet est
réimprimé avec son numéro ; sinon un nouveau numéro est attribué (compteur des relevés, annuel).
Sans cachet ni signature, sauf pour un super utilisateur qui le demande.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import Etudiant, Inscription, ReleveNote, Semestre, Service
from .pdf_releves import pdf_releve
from .releves import contenu, parcours, recherche, releve_existant
from .roles import scolarite_requise


def _entier(v):
    return int(v) if str(v or '').isdigit() else None


@login_required
@scolarite_requise
def releves(request):
    q = (request.GET.get('q') or '').strip()
    etudiant, candidats, inscriptions, programmes = None, [], [], []
    if _entier(request.GET.get('etudiant')):
        etudiant = get_object_or_404(Etudiant, pk=request.GET['etudiant'])
    elif q:
        candidats = list(Etudiant.objects.filter(recherche(q)).order_by('nom', 'prenom')[:30])
        if len(candidats) == 1:
            etudiant = candidats[0]
    if etudiant is not None:
        inscriptions, programmes = parcours(etudiant)
        deja = {(r.type, r.filiere_id, r.session_id, r.licence_id, r.semestre_id): r
                for r in ReleveNote.objects.filter(etudiant=etudiant)}
        for i in inscriptions:
            i.releve_annuel = deja.get((ReleveNote.ANNUEL, i.filiere_id, i.session_id, i.licence_id, None))
            i.releves_semestre = [(s, deja.get((ReleveNote.SEMESTRE, i.filiere_id, i.session_id, i.licence_id, s.pk)))
                                  for s in i.semestres_releve]
        for p in programmes:
            p.extrait = deja.get((ReleveNote.EXTRAIT, p.pk, None, None, None))

    r = (request.GET.get('r') or '').strip()
    delivres = ReleveNote.objects.select_related('etudiant', 'filiere', 'session', 'licence', 'semestre')
    if r:
        delivres = delivres.filter(Q(num_ref__icontains=r) | Q(etudiant__matricule__icontains=r)
                                   | Q(etudiant__nom__icontains=r) | Q(etudiant__prenom__icontains=r))
    page = Paginator(delivres, 25).get_page(request.GET.get('page'))
    return render(request, 'pfe/releves.html', {
        'q': q, 'r': r, 'etudiant': etudiant, 'candidats': candidats, 'inscriptions': inscriptions,
        'programmes': programmes, 'page': page, 'prochaine_ref': ReleveNote.prochaine_reference(),
        'actif': 'releves',
    })


@login_required
@scolarite_requise
@require_POST
def releve_creer(request):
    """Crée (ou retrouve) le relevé demandé, puis l'ouvre."""
    type_ = request.POST.get('type')
    if type_ == ReleveNote.EXTRAIT:
        etudiant = get_object_or_404(Etudiant, pk=_entier(request.POST.get('etudiant')))
        filiere = get_object_or_404(Service, pk=_entier(request.POST.get('programme')))
        if not Inscription.objects.filter(etudiant=etudiant, filiere=filiere).exists():
            raise Http404("Cet étudiant n'a aucune inscription dans ce programme.")
        cible = {'etudiant': etudiant, 'filiere': filiere}
    elif type_ in (ReleveNote.SEMESTRE, ReleveNote.ANNUEL):
        insc = get_object_or_404(Inscription.objects.select_related('etudiant'), pk=_entier(request.POST.get('inscription')),
                                 filiere__isnull=False)
        cible = {'etudiant': insc.etudiant, 'filiere': insc.filiere, 'session': insc.session, 'licence': insc.licence}
        if type_ == ReleveNote.SEMESTRE:
            cible['semestre'] = get_object_or_404(Semestre, pk=_entier(request.POST.get('semestre')),
                                                  licence=insc.licence)
    else:
        raise Http404()
    releve = releve_existant(type_, **cible)
    if releve is None:
        releve = ReleveNote.objects.create(type=type_, **cible)
        messages.success(request, f"Relevé n° {releve.num_ref} délivré à {releve.etudiant.prenom} {releve.etudiant.nom}.")
    url = reverse('releve_pdf', args=[releve.pk])
    return redirect(url + ('?signature=1' if request.POST.get('signature') == '1' else ''))


@login_required
@scolarite_requise
def releve_pdf(request, pk):
    from .views import url_verification
    releve = get_object_or_404(ReleveNote.objects.select_related('etudiant', 'filiere', 'session', 'licence',
                                                                   'semestre'), pk=pk)
    # Cachet et signature numérisés : UNIQUEMENT un super utilisateur qui le demande
    signee = request.user.is_superuser and request.GET.get('signature') == '1'
    try:
        donnees = contenu(releve)
    except ValueError as e:
        raise Http404(str(e))
    reponse = HttpResponse(pdf_releve(releve, donnees, url_verification(request, releve), cachet_signature=signee),
                           content_type='application/pdf')
    nom = f"{releve.get_type_display()}_{releve.etudiant.matricule or releve.pk}" + ('_signe' if signee else '')
    reponse['Content-Disposition'] = f'inline; filename="{slugify(nom)}.pdf"'
    reponse['Cache-Control'] = 'no-store, private'
    return reponse


@login_required
@scolarite_requise
@require_POST
def releve_supprimer(request, pk):
    releve = get_object_or_404(ReleveNote, pk=pk)
    num = releve.num_ref
    releve.delete()
    messages.success(request, f"Relevé n° {num} supprimé. Ce numéro ne sera pas réutilisé.")
    return redirect('releves')
