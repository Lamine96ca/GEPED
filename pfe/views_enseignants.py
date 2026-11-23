"""
Enseignants : liste (par département), ajout, modification, activation du compte, et l'espace enseignant.

- La Direction Générale des Études gère (ajoute, modifie, désactive, envoie le lien d'activation) : c'est sa seule
  exception à la consultation seule (pfe/lecture_seule.py). Le super utilisateur aussi.
- La Direction Générale et la scolarité voient tous les enseignants ; le chef de département et le directeur de
  programme, ceux de leur département. En lecture seule.
- Un seul compte par personne : un membre du personnel qui enseigne garde son compte (voir EnseignantForm).
- À l'enregistrement, aucun e-mail n'est envoyé sauf si la case est cochée ; le lien d'activation peut être envoyé
  ensuite, comme pour le personnel.
"""
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .comptes import envoyer_mail_enseignant, statut as statut_compte
from .forms import EnseignantForm
from .models import Enseignant
from .roles import (_calculer_profil, departements_du_profil, departements_etablissement, enseignants_gestion_requise,
                    enseignants_lecture_requise, enseignants_visibles, espace_enseignant_requis, get_profil)

ETATS = [('actifs', 'Actifs'), ('desactives', 'Désactivés'), ('tous', 'Tous')]


def _entier(v):
    return int(v) if str(v or '').isdigit() else None


def _filtres(request, profil):
    g = request.GET
    f = {'q': (g.get('q') or '').strip(), 'departement': _entier(g.get('departement')),
         'statut': g.get('statut') if g.get('statut') in dict(Enseignant.STATUTS) else '',
         'etat': g.get('etat') if g.get('etat') in dict(ETATS) else 'actifs'}
    departements = departements_etablissement()
    if profil.role in ('chef_departement', 'programme') and not (profil.est_scolarite or profil.gere_enseignants):
        departements = departements.filter(pk__in=departements_du_profil(profil))
    return f, list(departements)


def _liste(profil, f):
    qs = enseignants_visibles(profil)
    if f['q']:
        for terme in f['q'].split()[:4]:
            qs = qs.filter(Q(prenom__icontains=terme) | Q(nom__icontains=terme) | Q(email__icontains=terme)
                           | Q(telephone__icontains=terme) | Q(specialite__icontains=terme)
                           | Q(utilisateur__username__icontains=terme))
    if f['departement']:
        qs = qs.filter(departement_id=f['departement'])
    if f['statut']:
        qs = qs.filter(statut=f['statut'])
    if f['etat'] == 'actifs':
        qs = qs.filter(actif=True)
    elif f['etat'] == 'desactives':
        qs = qs.filter(actif=False)
    return qs.select_related('utilisateur__profil_personnel').order_by('departement__nom', 'nom', 'prenom')


def _completer(e):
    """Pour l'affichage : statut du compte, et la fonction si la personne est aussi du personnel."""
    e.compte, e.compte_libelle = statut_compte(e.utilisateur)
    e.personnel = getattr(e.utilisateur, 'profil_personnel', None)
    return e


@login_required
@enseignants_lecture_requise
def enseignants_liste(request):
    profil = get_profil(request)
    f, departements = _filtres(request, profil)
    qs = _liste(profil, f)
    if request.GET.get('export') == 'xlsx':
        return _export(qs)
    if request.GET.get('imprimer') == 'pdf':
        return _imprimer(request, f, departements, qs)
    page = Paginator([_completer(e) for e in qs], 50).get_page(request.GET.get('page'))
    params = request.GET.copy()
    for cle in ('page', 'export', 'imprimer'):
        params.pop(cle, None)
    return render(request, 'pfe/enseignants_liste.html', {
        'page': page, 'filtres': f, 'departements': departements, 'statuts': Enseignant.STATUTS, 'etats': ETATS,
        'params': params.urlencode(), 'nb_total': enseignants_visibles(profil).filter(actif=True).count(),
        'actif': 'enseignants',
    })


COLONNES = ['N°', 'Civilité', 'Prénom', 'Nom', 'Grade', 'Statut', 'Département', 'Spécialité', 'Téléphone', 'E-mail']


def _rang(k, e):
    return [k, e.civilite, e.prenom, e.nom, e.get_grade_display(), e.get_statut_display(), e.departement.nom,
            e.specialite, e.telephone, e.email]


def _export(qs):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Enseignants'
    ws.append(COLONNES)
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C')
    for k, e in enumerate(qs, start=1):
        ws.append(_rang(k, e))
    for col, largeur in zip('ABCDEFGHIJ', (5, 9, 22, 18, 22, 12, 34, 30, 16, 32)):
        ws.column_dimensions[col].width = largeur
    flux = io.BytesIO()
    wb.save(flux)
    reponse = HttpResponse(flux.getvalue(),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    reponse['Content-Disposition'] = 'attachment; filename="enseignants.xlsx"'
    return reponse


def _imprimer(request, f, departements, qs):
    from .pdf_listes import imprimer_tableau
    filtres = [('Département', next((d.nom for d in departements if d.pk == f['departement']), None)),
               ('Statut', dict(Enseignant.STATUTS).get(f['statut'])),
               ('Enseignants', dict(ETATS)[f['etat']] if f['etat'] != 'tous' else None), ('Recherche', f['q'])]
    colonnes = [('N°', 18), ('Enseignant', 130), ('Grade', 80), ('Statut', 52), ('Département', 130),
                ('Spécialité', 110), ('Téléphone', 70), ('E-mail', 130)]
    rangs = [[k, ' '.join(x for x in (e.civilite, e.prenom, e.nom) if x), e.get_grade_display(),
              e.get_statut_display(), e.departement.nom, e.specialite, e.telephone, e.email]
             for k, e in enumerate(qs, start=1)]
    total = f"{len(rangs)} enseignant{'s' if len(rangs) > 1 else ''}"
    return imprimer_tableau(request, 'enseignants', 'LISTE DES ENSEIGNANTS', filtres, colonnes, rangs, paysage=True,
                            total=total, vide='Aucun enseignant avec ces filtres.')


def _envoyer(request, enseignant, quoi='activation'):
    """E-mail de la Direction des Études ; il précise si l'enseignant avait déjà un compte (inutile d'en créer un)."""
    if quoi == 'activation' and enseignant.utilisateur.has_usable_password():
        quoi = 'acces'
    try:
        adresse = envoyer_mail_enseignant(request, enseignant, quoi)
    except Exception as e:                                   # SMTP indisponible, adresse vide…
        messages.error(request, f"L'e-mail n'a pas pu être envoyé ({e}). Réessayez plus tard.")
        return False
    existant = " (compte existant : l'e-mail précise qu'il ne lui faut pas d'autre compte)" if enseignant.compte_existant else ""
    messages.success(request, {'activation': f"Lien d'activation du compte envoyé à {adresse}{existant}.",
                               'acces': f"E-mail d'information envoyé à {adresse} : son accès enseignant est ouvert "
                                        f"sur son compte existant, inutile d'en créer un autre.",
                               'reinitialiser': f"Lien de nouveau mot de passe envoyé à {adresse}."}[quoi])
    return True


def _formulaire(request, enseignant=None):
    if request.method == 'POST':
        form = EnseignantForm(request.POST, instance=enseignant)
        if form.is_valid():
            rattache = form.compte_existant is not None
            e = form.save(cree_par=request.user)
            if enseignant is not None:
                messages.success(request, f"Fiche de {e.prenom} {e.nom} enregistrée.")
            else:
                if rattache:
                    messages.success(request, f"{e.prenom} {e.nom} est enregistré(e) comme enseignant(e), sur son "
                                              f"compte existant (identifiant : {e.utilisateur.username}) : un seul compte.")
                else:
                    messages.success(request, f"Enseignant(e) enregistré(e) : {e.prenom} {e.nom} (identifiant : "
                                              f"{e.utilisateur.username}).")
                if form.cleaned_data.get('envoyer_invitation'):
                    _envoyer(request, e)
                else:
                    messages.info(request, "Aucun e-mail envoyé : utilisez « Envoyer l'e-mail » depuis la liste quand "
                                           "vous serez prêt.")
            return redirect('enseignants_liste')
    else:
        form = EnseignantForm(instance=enseignant)
    contexte = {'form': form, 'enseignant': enseignant, 'actif': 'enseignants',
                'titre': f"{enseignant.prenom} {enseignant.nom}" if enseignant else 'Nouvel enseignant'}
    if enseignant is not None:
        _completer(enseignant)
        contexte['role'] = _calculer_profil(enseignant.utilisateur).libelle
    return render(request, 'pfe/enseignant_form.html', contexte)


@login_required
@enseignants_gestion_requise
def enseignant_creer(request):
    return _formulaire(request)


@login_required
@enseignants_gestion_requise
def enseignant_modifier(request, pk):
    return _formulaire(request, get_object_or_404(Enseignant.objects.select_related('utilisateur'), pk=pk))


@login_required
@enseignants_gestion_requise
@require_POST
def enseignant_action(request, pk):
    """Envoyer le lien d'activation, l'information (compte existant) ou un lien de nouveau mot de passe ;
    désactiver ou réactiver l'enseignant."""
    e = get_object_or_404(Enseignant.objects.select_related('utilisateur'), pk=pk)
    action, u = request.POST.get('action'), e.utilisateur
    if action == 'desactiver':
        e.actif = False
        e.save(update_fields=['actif'])
        messages.success(request, f"{e.prenom} {e.nom} n'a plus accès à l'espace enseignant"
                                  f"{' (son compte du personnel reste actif)' if hasattr(u, 'profil_personnel') else ''}.")
    elif action == 'reactiver':
        e.actif = True
        e.save(update_fields=['actif'])
        messages.success(request, f"{e.prenom} {e.nom} a de nouveau accès à l'espace enseignant.")
    elif action in ('invitation', 'acces', 'reinitialiser'):
        if not u.is_active:
            messages.error(request, "Ce compte est désactivé (gestion du personnel) : aucun e-mail envoyé.")
        elif not e.actif:
            messages.error(request, "Cet enseignant est désactivé : réactivez-le avant d'envoyer un lien.")
        else:
            _envoyer(request, e, {'invitation': 'activation'}.get(action, action))
    suivant = request.POST.get('suivant', '')
    return redirect(suivant if suivant.startswith('/') and not suivant.startswith('//') else reverse('enseignants_liste'))


# ------------------------------------------------------------------ espace enseignant

@login_required
@espace_enseignant_requis
def espace_enseignant(request):
    """Accueil de l'espace enseignant (le contenu — emploi du temps… — viendra ensuite)."""
    profil = get_profil(request)
    return render(request, 'pfe/espace_enseignant.html', {'enseignant': profil.enseignant, 'actif': 'espace_enseignant'})
