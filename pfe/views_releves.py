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

from .forms import ReleveForm
from .models import Etudiant, Inscription, ReglageAffichage, ReleveNote, Semestre, Service
from .pdf_releves import pdf_releve
from .releves import a_des_notes, contenu, parcours, perimetres_notes, recherche, releve_existant
from .roles import scolarite_requise


def _entier(v):
    return int(v) if str(v or '').isdigit() else None


@login_required
@scolarite_requise
def releves(request):
    # Formulaire « Nouveau relevé » : enregistre (ou reprend) le relevé, puis propose de l'imprimer
    if request.method == 'POST':
        form = ReleveForm(request.POST)
        if form.is_valid():
            releve, cree = form.save()
            e = releve.etudiant
            if cree:
                messages.success(request, f"Relevé n° {releve.num_ref} enregistré pour {e.prenom} {e.nom}.")
            else:
                messages.info(request, f"Ce relevé a déjà été délivré à {e.prenom} {e.nom} : n° {releve.num_ref} "
                                       f"(même numéro, notes à jour à l'impression).")
            return redirect(f"{reverse('releves')}?etudiant={e.pk}&imprimer={releve.pk}")
    else:
        initial = {}
        if _entier(request.GET.get('etudiant')):
            e = Etudiant.objects.filter(pk=request.GET['etudiant']).first()
            initial['matricule'] = e.matricule if e else ''
        form = ReleveForm(initial=initial)

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
        notes = perimetres_notes(etudiant)                     # boutons grisés là où il n'y a aucune note
        for i in inscriptions:
            i.releve_annuel = deja.get((ReleveNote.ANNUEL, i.filiere_id, i.session_id, i.licence_id, None))
            i.releves_semestre = [(s, deja.get((ReleveNote.SEMESTRE, i.filiere_id, i.session_id, i.licence_id, s.pk)),
                                   (i.filiere_id, i.session_id, i.licence_id, s.pk) in notes)
                                  for s in i.semestres_releve]
            i.a_des_notes = any(n for _, _, n in i.releves_semestre)
        for p in programmes:
            p.extrait = deja.get((ReleveNote.EXTRAIT, p.pk, None, None, None))
            p.a_des_notes = any(n[0] == p.pk for n in notes)

    r = (request.GET.get('r') or '').strip()
    delivres = ReleveNote.objects.select_related('etudiant', 'filiere', 'session', 'licence', 'semestre')
    total = ReleveNote.objects.count()
    if r:
        # La recherche porte sur tous les relevés, même ceux que la liste n'affiche pas
        delivres = delivres.filter(Q(num_ref__icontains=r) | Q(etudiant__matricule__icontains=r)
                                   | Q(etudiant__nom__icontains=r) | Q(etudiant__prenom__icontains=r))
        page = Paginator(delivres, 25).get_page(request.GET.get('page'))
        limite = None
    else:
        # Sans recherche : les N derniers seulement, N fixé dans l'espace admin (Réglages d'affichage)
        limite = ReglageAffichage.actuel().nb_releves
        page = Paginator(delivres[:limite], limite).get_page(1)
    imprimer = None
    if _entier(request.GET.get('imprimer')):
        imprimer = ReleveNote.objects.select_related('etudiant', 'filiere', 'session', 'semestre', 'licence').filter(
            pk=request.GET['imprimer']).first()
    return render(request, 'pfe/releves.html', {
        'form': form, 'q': q, 'r': r, 'etudiant': etudiant, 'candidats': candidats, 'inscriptions': inscriptions,
        'programmes': programmes, 'page': page, 'prochaine_ref': ReleveNote.prochaine_reference(),
        'imprimer': imprimer, 'actif': 'releves', 'total': total, 'limite': limite,
        'semestres_par_licence': {str(s.pk): str(s.licence_id) for s in Semestre.objects.all()},
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
        if not a_des_notes(**cible):
            e = cible['etudiant']
            messages.error(request, f"{e.prenom} {e.nom} n'a aucune note pour ce relevé : il n'est pas enregistré.")
            return redirect(f"{reverse('releves')}?etudiant={e.pk}")
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
