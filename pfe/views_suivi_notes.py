"""
Suivi des notes (scolarité, administrateur, Direction Générale des Études) :
- historique des modifications (avant / après, auteur, motif, justificatif, import d'origine) ;
- historique d'une note : sa première saisie et toutes ses modifications ;
- fichiers importés (Excel et PDF de l'enseignant), avec le service de la personne qui importe :
  téléchargeables, jamais remplacés ;
- autorisations de modification accordées aux programmes par le chef de la scolarité
  (programmes, session, niveaux, du … au …) pour les semestres délibérés et les années écoulées.
"""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import AutorisationNotes, ImportNotes, Licence, Note, NoteHistorique, Service, Session, stockage_prive
from .notes import avec_promotion, filtrer_promotion
from .roles import chef_scolarite_requis, get_profil, scolarite_requise, suivi_notes_requis
from .views_notes import promotions_de

LIBELLES = [('controle', 'Contrôle'), ('tp', 'TP'), ('examen', 'Examen'), ('moyenne', 'Moyenne'),
            ('rattrapage', 'Rattrapage'), ('moyenne_finale', 'Finale')]


def _entier(valeur):
    return int(valeur) if str(valeur or '').isdigit() else None


def _date(valeur):
    try:
        return datetime.date.fromisoformat((valeur or '').strip())
    except ValueError:
        return None


def _params(request):
    params = request.GET.copy()
    params.pop('page', None)
    return params.urlencode()


def _ecarts(h):
    """Champs qui ont changé : [(libellé, avant, après)]."""
    avant, apres = h.avant or {}, h.apres or {}
    return [(lib, avant.get(c), apres.get(c)) for c, lib in LIBELLES if avant.get(c) != apres.get(c)]


# ------------------------------------------------------------------ historique

@login_required
@suivi_notes_requis
def notes_historique(request):
    """Toutes les modifications de notes : session, programme, auteur, après délibération, recherche ; 25 par page."""
    g = request.GET
    f = {'session': _entier(g.get('session')), 'programme': _entier(g.get('programme')),
         'promotion': _entier(g.get('promotion')), 'auteur': _entier(g.get('auteur')),
         'delibere': g.get('delibere') == '1', 'q': (g.get('q') or '').strip()}
    qs = NoteHistorique.objects.select_related(
        'utilisateur', 'note__etudiant', 'note__matiere__filiere', 'note__matiere__session',
        'note__matiere__licence', 'note__matiere__semestre', 'import_notes')
    if f['session']:
        qs = qs.filter(note__matiere__session_id=f['session'])
    if f['programme']:
        qs = qs.filter(note__matiere__filiere_id=f['programme'])
    if f['promotion']:
        qs = filtrer_promotion(qs, f['promotion'], 'note__')
    qs = avec_promotion(qs, 'note__')
    if f['auteur']:
        qs = qs.filter(utilisateur_id=f['auteur'])
    if f['delibere']:
        qs = qs.filter(apres_deliberation=True)
    for terme in f['q'].split()[:4]:
        qs = qs.filter(Q(note__etudiant__matricule__icontains=terme) | Q(note__etudiant__nom__icontains=terme)
                       | Q(note__etudiant__prenom__icontains=terme) | Q(note__matiere__nom__icontains=terme)
                       | Q(motif__icontains=terme))
    page = Paginator(qs, 25).get_page(g.get('page'))
    for h in page:
        h.ecarts = _ecarts(h)
    from django.contrib.auth import get_user_model
    auteurs = get_user_model().objects.filter(
        pk__in=NoteHistorique.objects.values('utilisateur_id')).order_by('last_name', 'first_name', 'username')
    return render(request, 'pfe/notes_historique.html', {
        'page': page, 'f': f, 'params': _params(request), 'auteurs': auteurs,
        'sessions': Session.objects.order_by('-annee'),
        'promotions': promotions_de({'session': f['session'], 'programme': f['programme'],
                                     'promotion': f['promotion']}),
        'programmes': Service.objects.filter(programme=True).order_by('nom'), 'actif': 'notes_historique',
    })


@login_required
@suivi_notes_requis
def note_historique(request, pk):
    """Une note : sa première saisie (auteur, import d'origine) et toutes ses modifications."""
    note = get_object_or_404(Note.objects.select_related(
        'etudiant', 'matiere__filiere', 'matiere__session', 'matiere__licence', 'matiere__semestre', 'saisie_par',
        'import_saisie__utilisateur'), pk=pk)
    modifications = list(note.historique.select_related('utilisateur', 'import_notes__utilisateur'))
    for h in modifications:
        h.ecarts = _ecarts(h)
    promotion = avec_promotion(Note.objects.filter(pk=note.pk)).values_list('promotion_nom', flat=True).first()
    return render(request, 'pfe/note_historique.html', {
        'note': note, 'modifications': modifications, 'libelles': LIBELLES, 'actif': 'notes_historique',
        'promotion': promotion,
    })


@login_required
@suivi_notes_requis
def notes_historique_justificatif(request, pk):
    """Justificatif PDF d'une modification de note (stockage privé, jamais public)."""
    h = get_object_or_404(NoteHistorique, pk=pk)
    if not h.justificatif:
        raise Http404
    nom = h.justificatif.name.rsplit('/', 1)[-1]
    reponse = FileResponse(stockage_prive().open(h.justificatif.name, 'rb'), content_type='application/pdf',
                           filename=nom)
    reponse['Content-Disposition'] = f'inline; filename="{nom}"'
    reponse['X-Content-Type-Options'] = 'nosniff'
    reponse['Cache-Control'] = 'private, no-store'
    return reponse


# ------------------------------------------------------------------ fichiers importés

@login_required
@suivi_notes_requis
def notes_imports(request):
    """Imports de notes : fichier Excel et PDF de l'enseignant, personne et service qui importent ; 25 par page."""
    g = request.GET
    f = {'service': _entier(g.get('service')), 'q': (g.get('q') or '').strip()}
    qs = ImportNotes.objects.select_related('utilisateur').prefetch_related('services')
    if f['service']:
        qs = qs.filter(services__id=f['service'])
    for terme in f['q'].split()[:4]:
        qs = qs.filter(Q(fichier__icontains=terme) | Q(programmes__icontains=terme)
                       | Q(utilisateur__username__icontains=terme) | Q(utilisateur__last_name__icontains=terme)
                       | Q(utilisateur__first_name__icontains=terme))
    page = Paginator(qs.distinct(), 25).get_page(g.get('page'))
    return render(request, 'pfe/notes_imports.html', {
        'page': page, 'f': f, 'params': _params(request),
        'services': Service.objects.filter(imports_notes__isnull=False).distinct().order_by('nom'),
        'actif': 'notes_imports',
    })


# ------------------------------------------------------------------ autorisations

@login_required
@scolarite_requise
def notes_autorisations(request):
    """Autorisations de modification des notes (semestres délibérés, années écoulées) pour les programmes."""
    from django.utils import timezone
    aujourd_hui = timezone.localdate()
    qs = AutorisationNotes.objects.select_related('session', 'accordee_par').prefetch_related('programmes', 'licences')
    etat = request.GET.get('etat') if request.GET.get('etat') in ('en_cours', 'a_venir', 'terminees') else ''
    if etat == 'en_cours':
        qs = qs.filter(date_debut__lte=aujourd_hui, date_fin__gte=aujourd_hui)
    elif etat == 'a_venir':
        qs = qs.filter(date_debut__gt=aujourd_hui)
    elif etat == 'terminees':
        qs = qs.filter(date_fin__lt=aujourd_hui)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    sessions = Session.objects.order_by('-annee')
    courante = sessions.filter(en_cours=True).first() or sessions.first()
    return render(request, 'pfe/notes_autorisations.html', {
        'page': page, 'etat': etat, 'sessions': sessions, 'session_defaut': courante.pk if courante else None,
        'programmes': Service.objects.filter(programme=True).order_by('nom'),
        'licences': Licence.objects.order_by('id'), 'aujourd_hui': aujourd_hui,
        'actif': 'notes_autorisations',
    })


@login_required
@chef_scolarite_requis
@require_POST
def notes_autorisation_creer(request):
    p = request.POST
    programmes = list(Service.objects.filter(programme=True, pk__in=[v for v in p.getlist('programmes') if v.isdigit()]))
    licences = list(Licence.objects.filter(pk__in=[v for v in p.getlist('licences') if v.isdigit()]))
    session = Session.objects.filter(pk=_entier(p.get('session'))).first()
    debut, fin = _date(p.get('date_debut')), _date(p.get('date_fin'))
    erreurs = []
    if not programmes:
        erreurs.append("cochez au moins un programme")
    if session is None:
        erreurs.append("choisissez la session")
    if not licences:
        erreurs.append("cochez au moins un niveau")
    if debut is None or fin is None:
        erreurs.append("indiquez la date de début et la date de fin")
    elif fin < debut:
        erreurs.append("la date de fin doit suivre la date de début")
    if erreurs:
        messages.error(request, "Autorisation non enregistrée : " + ", ".join(erreurs) + ".")
        return redirect('notes_autorisations')
    a = AutorisationNotes.objects.create(session=session, date_debut=debut, date_fin=fin,
                                         motif=(p.get('motif') or '').strip(), accordee_par=request.user)
    a.programmes.set(programmes)
    a.licences.set(licences)
    messages.success(request, f"Autorisation accordée du {debut:%d/%m/%Y} au {fin:%d/%m/%Y} : "
                              f"{len(programmes)} programme(s), {', '.join(l.nom for l in licences)}, {session}.")
    return redirect('notes_autorisations')


@login_required
@chef_scolarite_requis
@require_POST
def notes_autorisation_clore(request, pk):
    """Clôt une autorisation (fin hier) ; une autorisation pas encore commencée est supprimée."""
    from django.utils import timezone
    a = get_object_or_404(AutorisationNotes, pk=pk)
    aujourd_hui = timezone.localdate()
    if a.date_debut >= aujourd_hui:
        a.delete()
        messages.success(request, "Autorisation supprimée.")
    elif a.date_fin >= aujourd_hui:
        a.date_fin = aujourd_hui - datetime.timedelta(days=1)
        a.save(update_fields=['date_fin'])
        messages.success(request, "Autorisation close : les programmes ne peuvent plus modifier ces notes.")
    return redirect('notes_autorisations')
