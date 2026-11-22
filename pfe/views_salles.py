"""
Salles et bâtiments, sur une même page : bâtiments à gauche (nombre de salles, places), salles à droite (filtres,
ajout et modification dans une fenêtre), import Excel / CSV (avec simulation), exports Excel, CSV et PDF.
Gestion : Direction Générale des Études et super utilisateur ; consultation : Direction Générale et scolarité.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import BatimentForm, ImportMatieresForm, SalleForm
from .models import Batiment, Salle
from .roles import salles_gestion_requise, salles_lecture_requise
from .salles import COLONNES, OBLIGATOIRES, export_csv, export_xlsx, importer_salles, modele_salles_xlsx

ETATS = [('disponibles', 'Disponibles'), ('indisponibles', 'Indisponibles'), ('toutes', 'Toutes')]
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _entier(v):
    return int(v) if str(v or '').isdigit() else None


def _filtres(request):
    g = request.GET
    return {'q': (g.get('q') or '').strip(), 'batiment': _entier(g.get('batiment')),
            'type': g.get('type') if g.get('type') in dict(Salle.TYPES) else '',
            'capacite': _entier(g.get('capacite')),
            'etat': g.get('etat') if g.get('etat') in dict(ETATS) else 'toutes'}


def _salles(f):
    qs = Salle.objects.select_related('batiment')
    if f['q']:
        qs = qs.filter(Q(nom__icontains=f['q']) | Q(equipements__icontains=f['q']) | Q(batiment__nom__icontains=f['q']))
    if f['batiment']:
        qs = qs.filter(batiment_id=f['batiment'])
    if f['type']:
        qs = qs.filter(type=f['type'])
    if f['capacite']:
        qs = qs.filter(capacite__gte=f['capacite'])
    if f['etat'] == 'disponibles':
        qs = qs.filter(disponible=True)
    elif f['etat'] == 'indisponibles':
        qs = qs.filter(disponible=False)
    return qs.order_by('batiment__nom', 'nom')


def _page(request, form_batiment=None, form_salle=None, ouvrir=''):
    """La page unique ; après une saisie refusée, la fenêtre concernée se rouvre avec ses erreurs."""
    f = _filtres(request)
    qs = _salles(f)
    if request.method == 'GET':
        export = request.GET.get('export')
        if export in ('xlsx', 'csv'):
            contenu, type_ = (export_xlsx(qs), XLSX) if export == 'xlsx' else (export_csv(qs), 'text/csv; charset=utf-8')
            reponse = HttpResponse(contenu, content_type=type_)
            reponse['Content-Disposition'] = f'attachment; filename="salles.{export}"'
            return reponse
        if request.GET.get('imprimer') == 'pdf':
            return _imprimer(request, f, qs)
    batiments = Batiment.objects.annotate(
        nb_salles=Count('salles'), places=Sum('salles__capacite', filter=Q(salles__disponible=True))).order_by('nom')
    salles = list(qs)
    params = request.GET.copy()
    for cle in ('export', 'imprimer'):
        params.pop(cle, None)
    return render(request, 'pfe/salles.html', {
        'batiments': batiments, 'salles': salles, 'filtres': f, 'types': Salle.TYPES, 'etats': ETATS,
        'params': params.urlencode(), 'nb_places': sum(s.capacite for s in salles if s.disponible),
        'form_batiment': form_batiment or BatimentForm(), 'form_salle': form_salle or SalleForm(
            initial={'batiment': f['batiment']} if f['batiment'] else None),
        'ouvrir': ouvrir, 'actif': 'salles',
    })


def _imprimer(request, f, qs):
    from .pdf_listes import imprimer_tableau
    filtres = [('Bâtiment', Batiment.objects.filter(pk=f['batiment']).values_list('nom', flat=True).first()
                if f['batiment'] else None),
               ('Type', dict(Salle.TYPES).get(f['type'])),
               ('Capacité', f"au moins {f['capacite']} places" if f['capacite'] else None),
               ('Salles', dict(ETATS)[f['etat']] if f['etat'] != 'toutes' else None), ('Recherche', f['q'])]
    colonnes = [('N°', 18), ('Bâtiment', 120), ('Salle', 110), ('Type', 95), ('Places', 45), ('Équipements', 170),
                ('Disponible', 55)]
    salles = list(qs)
    rangs = [[k, s.batiment.nom, s.nom, s.get_type_display(), s.capacite, s.equipements, 'oui' if s.disponible
              else 'non'] for k, s in enumerate(salles, start=1)]
    places = sum(s.capacite for s in salles if s.disponible)
    total = f"{len(salles)} salle{'s' if len(salles) > 1 else ''} · {places} places disponibles"
    return imprimer_tableau(request, 'salles', 'SALLES ET BÂTIMENTS', filtres, colonnes, rangs, paysage=True,
                            total=total, vide='Aucune salle avec ces filtres.')


@login_required
@salles_lecture_requise
def salles(request):
    return _page(request)


def _retour(request):
    suivant = request.POST.get('suivant', '')
    return redirect(suivant if suivant.startswith('/') and not suivant.startswith('//') else 'salles')


@login_required
@salles_gestion_requise
@require_POST
def batiment_enregistrer(request, pk=None):
    batiment = get_object_or_404(Batiment, pk=pk) if pk else None
    form = BatimentForm(request.POST, instance=batiment)
    if not form.is_valid():
        return _page(request, form_batiment=form, ouvrir=f'batiment-{pk or "nouveau"}')
    b = form.save()
    messages.success(request, f"Bâtiment « {b.nom} » {'modifié' if batiment else 'ajouté'}.")
    return _retour(request)


@login_required
@salles_gestion_requise
@require_POST
def batiment_supprimer(request, pk):
    b = get_object_or_404(Batiment, pk=pk)
    if b.salles.exists():
        messages.error(request, f"Le bâtiment « {b.nom} » a encore des salles : supprimez-les ou déplacez-les d'abord.")
    else:
        b.delete()
        messages.success(request, f"Bâtiment « {b.nom} » supprimé.")
    return _retour(request)


@login_required
@salles_gestion_requise
@require_POST
def salle_enregistrer(request, pk=None):
    salle = get_object_or_404(Salle, pk=pk) if pk else None
    form = SalleForm(request.POST, instance=salle)
    if not form.is_valid():
        return _page(request, form_salle=form, ouvrir=f'salle-{pk or "nouvelle"}')
    s = form.save()
    messages.success(request, f"Salle « {s.nom} » ({s.batiment.nom}) {'modifiée' if salle else 'ajoutée'}.")
    return _retour(request)


@login_required
@salles_gestion_requise
@require_POST
def salle_supprimer(request, pk):
    s = get_object_or_404(Salle.objects.select_related('batiment'), pk=pk)
    try:
        s.delete()
    except ProtectedError:                               # déjà utilisée (emploi du temps…) : on la rend indisponible
        messages.error(request, f"La salle « {s.nom} » est utilisée : décochez plutôt « Disponible ».")
    else:
        messages.success(request, f"Salle « {s.nom} » ({s.batiment.nom}) supprimée.")
    return _retour(request)


@login_required
@salles_gestion_requise
def salles_importer(request):
    rapport = None
    if request.method == 'POST':
        form = ImportMatieresForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                rapport = importer_salles(form.cleaned_data['fichier'], simulation=form.cleaned_data['simulation'])
            except ValueError as e:
                form.add_error('fichier', str(e))
            else:
                if not rapport.simulation and (rapport.creees or rapport.mises_a_jour):
                    messages.success(request, "Import des salles terminé et enregistré.")
    else:
        form = ImportMatieresForm()
    return render(request, 'pfe/salles_importer.html', {
        'form': form, 'rapport': rapport, 'obligatoires': OBLIGATOIRES, 'colonnes': COLONNES, 'actif': 'salles'})


@login_required
@salles_gestion_requise
def salles_modele(request):
    reponse = HttpResponse(modele_salles_xlsx(), content_type=XLSX)
    reponse['Content-Disposition'] = 'attachment; filename="modele_import_salles.xlsx"'
    return reponse
