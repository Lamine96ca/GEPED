"""
Espace enseignant, plans de cours et délégués de classe.

- Espace enseignant : son emploi du temps (séances de la session, impression), ses matières (celles de ses séances),
  ses heures par semaine d'après l'emploi du temps, la liste des étudiants de chaque matière (liste d'appel), la
  saisie des notes, la fiche Excel et l'import (pfe/views_notes.py, limités à ses matières), et ses plans de cours.
- Plans de cours (pfe/plans_cours.py) : dépôt du document Word, chapitres et contenus corrigés par l'enseignant,
  soumission ; approbation par le chef de département ou le directeur du programme ; puis, à chaque cours, les
  contenus dispensés cochés et signés par l'enseignant, validés par un délégué de la classe (lien reçu par e-mail,
  72 h) avec l'heure d'arrivée et l'heure de départ de l'enseignant : ce sont les heures faites (pfe/heures.py).
- Délégués de classe : deux au plus par classe (programme, niveau, session), désignés par le directeur du programme
  ou le chef de département.
"""
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import emplois, heures, plans_cours
from .forms import preparer_signature
from .models import (DelegueClasse, ElementPlan, Enseignant, Etudiant, Inscription, Licence, Matiere, ParametresHeures,
                     PlanCours, Seance, SeanceCours, Service, Session, stockage_prive)
from .roles import delegues_requis, espace_enseignant_requis, get_profil, plans_lecture_requise

PERIODES = [('impair', 'Semestres impairs (1, 3, 5…)'), ('pair', 'Semestres pairs (2, 4, 6…)')]


def _entier(v):
    return int(v) if str(v or '').isdigit() else None


def _session(request):
    sessions = list(Session.objects.order_by('-annee'))
    sid = _entier(request.GET.get('session'))
    choisie = (next((s for s in sessions if s.pk == sid), None) or next((s for s in sessions if s.en_cours), None)
               or (sessions[0] if sessions else None))
    return choisie, sessions


def fmt_heures(h):
    minutes = round(h * 60)
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes % 60 else f"{minutes // 60} h"


def _matiere_enseignee(request, pk):
    profil = get_profil(request)
    if pk not in profil.matieres_enseignees_ids:
        raise Http404
    return get_object_or_404(Matiere.objects.select_related('filiere', 'licence', 'semestre', 'session'), pk=pk)


# ------------------------------------------------------------------ données communes des pages de l'espace

def _seances(enseignant, session):
    seances = list(Seance.objects.filter(enseignants=enseignant, matiere__session=session)
                   .select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre', 'salle__batiment')
                   .prefetch_related('enseignants').order_by('jour', 'debut')) if session else []
    for s in seances:
        s.horaire = f"{Seance.heure(s.debut)} – {Seance.heure(s.fin)}"
        s.type_court = Seance.COURTS.get(s.type, s.type)
    return seances


def _blocs(seances):
    """Par période (semestres impairs / pairs) : séances, heures par semaine par matière et type, total."""
    blocs = []
    for code, libelle in PERIODES:
        parite = 1 if code == 'impair' else 0
        les_seances = [s for s in seances if emplois.periode(s.matiere.semestre) in (parite, None)]
        if not les_seances:
            continue
        heures = defaultdict(float)
        for s in les_seances:
            heures[(s.matiere_id, s.type)] += (s.fin - s.debut) / 60
        detail = []
        for (mid, typ), h in heures.items():
            m = next(s.matiere for s in les_seances if s.matiere_id == mid)
            detail.append({'matiere': m, 'type': Seance.COURTS.get(typ, typ), 'heures': fmt_heures(h),
                           'classe': emplois.libelle_classe(m)})
        detail.sort(key=lambda d: (d['matiere'].nom, d['type']))
        par_type = defaultdict(float)
        for (_, typ), h in heures.items():
            par_type[Seance.COURTS.get(typ, typ)] += h
        total = sum(heures.values())
        blocs.append({'code': code, 'libelle': libelle, 'seances': les_seances, 'detail': detail,
                      'par_type': [(t, fmt_heures(h)) for t, h in sorted(par_type.items())],
                      'total': fmt_heures(total), 'total_heures': total})
    return blocs


def _matieres(profil, session):
    """Ses matières de la session (celles de ses séances) : effectif, notes saisies, plan de cours."""
    from .models import Note
    if not session:
        return []
    matieres = list(Matiere.objects.filter(pk__in=profil.matieres_enseignees_ids, session=session)
                    .select_related('filiere', 'licence', 'semestre', 'session').order_by('semestre__nom', 'nom'))
    plans = {p.matiere_id: p for p in PlanCours.objects.filter(enseignant=profil.enseignant, matiere__in=matieres)
             .prefetch_related('parties')}
    lignes = []
    for m in matieres:
        effectif = Inscription.objects.filter(filiere_id=m.filiere_id, session_id=m.session_id,
                                              licence_id=m.licence_id, etudiant__preinscrit=False,
                                              conge__isnull=True).count()
        notes = Note.objects.filter(matiere=m, moyenne_finale__isnull=False).count()
        plan = plans.get(m.pk)
        lignes.append({'matiere': m, 'effectif': effectif, 'notes': notes,
                       'pct_notes': round(100 * min(notes, effectif) / effectif) if effectif else 0,
                       'plan': plan, 'avancement': plan.avancement() if plan else None,
                       'classe': emplois.libelle_classe(m)})
    return lignes


def _page(request, gabarit, actif, **contexte):
    profil = get_profil(request)
    session, sessions = _session(request)
    return render(request, gabarit, {'enseignant': profil.enseignant, 'session': session, 'sessions': sessions,
                                     'actif': actif, **{k: (v(profil, session) if callable(v) else v)
                                                       for k, v in contexte.items()}})


# ------------------------------------------------------------------ pages de l'espace enseignant

@login_required
@espace_enseignant_requis
def espace_enseignant(request):
    """Tableau de bord : chiffres clés de la session (matières, étudiants, heures, notes, plans de cours)."""
    profil = get_profil(request)
    session, sessions = _session(request)
    lignes = _matieres(profil, session)
    blocs = _blocs(_seances(profil.enseignant, session))
    attente = SeanceCours.objects.filter(enseignant=profil.enseignant, matiere__session=session,
                                         statut__in=SeanceCours.EN_COURS).count() if session else 0
    a_resigner = SeanceCours.objects.filter(enseignant=profil.enseignant, statut=SeanceCours.A_RESIGNER).count()
    chiffres = {
        'matieres': len(lignes),
        'etudiants': sum(l['effectif'] for l in lignes),
        'heures': fmt_heures(max([b['total_heures'] for b in blocs] or [0])),
        'notes_manquantes': sum(max(0, l['effectif'] - l['notes']) for l in lignes),
        'plans_a_deposer': sum(1 for l in lignes if not l['plan'] or l['plan'].statut in ('brouillon', 'refuse')),
        'plans_soumis': sum(1 for l in lignes if l['plan'] and l['plan'].statut == 'soumis'),
        'plans_approuves': sum(1 for l in lignes if l['plan'] and l['plan'].statut == 'approuve'),
        'parties_attente': attente,
        'a_resigner': a_resigner,
    }
    return render(request, 'pfe/enseignant/accueil.html', {
        'enseignant': profil.enseignant, 'session': session, 'sessions': sessions, 'lignes': lignes,
        'chiffres': chiffres, 'refuses': [l for l in lignes if l['plan'] and l['plan'].statut == 'refuse'],
        'actif': 'ens_accueil'})


@login_required
@espace_enseignant_requis
def enseignant_emploi(request):
    """Son emploi du temps de la session, par période ; impression de la grille."""
    profil = get_profil(request)
    session, sessions = _session(request)
    seances = _seances(profil.enseignant, session)
    if request.GET.get('imprimer') == 'pdf' and session:
        from .views_emplois import _imprimer
        periode = request.GET.get('periode') if request.GET.get('periode') in dict(PERIODES) else 'impair'
        parite = 1 if periode == 'impair' else 0
        choisies = [s for s in seances if emplois.periode(s.matiere.semestre) in (parite, None)]
        sel = {'mode': 'enseignant', 'enseignant': profil.enseignant, 'session': session, 'periode': periode}
        return _imprimer(request, sel, emplois.avec_conflits(choisies, session.pk))
    blocs = _blocs(seances)
    for b in blocs:
        # Grille de la semaine : sur chaque cours, la classe (et non les enseignants) ; pas d'alerte de conflit ici
        b['dispo'] = emplois.disposition([(x, []) for x in b['seances']])
        for d in b['dispo']:
            d['enseignants'] = d['enseignants_complets'] = emplois.libelle_classe(d['s'].matiere)
    return render(request, 'pfe/enseignant/emploi.html', {
        'enseignant': profil.enseignant, 'session': session, 'sessions': sessions, 'blocs': blocs,
        'jours': Seance.JOURS, 'creneaux': emplois.creneaux(), 'actif': 'ens_emploi'})


@login_required
@espace_enseignant_requis
def enseignant_matieres(request):
    return _page(request, 'pfe/enseignant/matieres.html', 'ens_matieres', lignes=_matieres)


@login_required
@espace_enseignant_requis
def enseignant_classes(request):
    """Mes étudiants : ses classes (programme, niveau) et, pour chacune, ses matières et la liste des étudiants."""
    def classes(profil, session):
        par_classe = {}
        for l in _matieres(profil, session):
            m = l['matiere']
            c = par_classe.setdefault((m.filiere_id, m.licence_id), {'classe': l['classe'], 'effectif': l['effectif'],
                                                                   'matieres': []})
            c['matieres'].append(m)
        return sorted(par_classe.values(), key=lambda c: c['classe'])
    return _page(request, 'pfe/enseignant/classes.html', 'ens_etudiants', classes=classes)


@login_required
@espace_enseignant_requis
def enseignant_notes(request):
    return _page(request, 'pfe/enseignant/notes.html', 'ens_notes', lignes=_matieres)


@login_required
@espace_enseignant_requis
def enseignant_plans(request):
    return _page(request, 'pfe/enseignant/plans.html', 'ens_plans', lignes=_matieres)


@login_required
@espace_enseignant_requis
def enseignant_heures(request):
    """
    Ses heures : par semaine d'après l'emploi du temps (semaine type), par période, matière et type ; et, par
    matière, les heures prévues (emploi du temps × semaines) comparées aux heures faites (cours validés).
    """
    def blocs(profil, session):
        les_blocs = _blocs(_seances(profil.enseignant, session))
        if not session:
            return les_blocs
        matieres = {m.pk: m for m in Matiere.objects.filter(pk__in=profil.matieres_enseignees_ids, session=session)
                    .select_related('filiere', 'licence', 'semestre')}
        for b in les_blocs:
            lignes, semaines = heures.comparaison(session, b['code'], [profil.enseignant])
            lignes = [l for l in lignes if l['matiere_id'] in matieres]
            for l in lignes:
                l['matiere'] = matieres[l['matiere_id']]
                l['nom'], l['detail'] = l['matiere'].nom, emplois.libelle_classe(l['matiere'])
            b['comparaison'] = sorted(heures.avec_barres(lignes), key=lambda l: l['matiere'].nom)
            b['semaines'] = semaines
            b['fait_total'] = heures.fmt(sum(l['fait'] for l in lignes))
            b['prevu_total'] = heures.fmt(sum(l['reference'] for l in lignes))
        return les_blocs
    return _page(request, 'pfe/enseignant/heures.html', 'ens_heures', blocs=blocs,
                 semaines_semestre=lambda p, s: ParametresHeures.actuels().semaines_semestre)


@login_required
@espace_enseignant_requis
def enseignant_heures_sup(request):
    """
    Ses heures supplémentaires (ou sa vacation) de l'année, expliquées pas à pas avec SES chiffres : heures faites,
    équivalent TD, charge statutaire, dépassement, plafonds, montant (arrêté A/2024/108 et décret D/2024/0027).
    """
    profil = get_profil(request)
    session, sessions = _session(request)
    params = ParametresHeures.actuels()
    ligne = None
    if session:
        permanents, vacataires = heures.situation(session, [profil.enseignant], params)
        ligne = (permanents + vacataires)[0] if permanents + vacataires else None
    g = params.grade(profil.enseignant.grade)
    # Exemple : un maître-assistant qui a enseigné 80 h de CM, 100 h de TD et 60 h de TP dans l'année
    ma = params.grade('maitre_assistant')
    eq = 80 * params.coefficient('CM') + 100 + 60 * params.coefficient('TP')
    charge = float(ma['charge'])
    depasse = max(0.0, eq - charge)
    hs = min(depasse, charge * params.hs_plafond_pct / 100)
    exemple = {'cm': 80, 'td': 100, 'tp': 60, 'cm_eq': 80 * params.coefficient('CM'), 'tp_eq': 60 * params.coefficient('TP'),
               'eq': eq, 'charge': charge, 'depasse': depasse, 'moitie': charge * params.hs_plafond_pct / 100, 'hs': hs}
    return render(request, 'pfe/enseignant/heures_sup.html', {
        'enseignant': profil.enseignant, 'session': session, 'sessions': sessions, 'l': ligne, 'params': params,
        'grade': g, 'charge_grade_txt': heures.fmt(g['charge']) if g['charge'] else None,
        'charge_semaine_txt': heures.fmt(float(g['charge']) / params.semaines_annee) if g['charge'] else None,
        'exemple': {**{k: heures.fmt(v) for k, v in exemple.items()}, 'taux': heures.gnf(ma['taux']),
                    'montant': heures.gnf(hs * ma['taux'])},
        'grades': [(lib, heures.fmt(params.grade(code)['charge']), heures.gnf(params.grade(code)['taux']))
                   for code, lib in Enseignant.GRADES if code in params.GRADES_TEXTES],
        'actif': 'ens_heures_sup'})


@login_required
@espace_enseignant_requis
def enseignant_etudiants(request, pk):
    """Étudiants de la matière (inscrits de la classe) ; liste d'appel à imprimer."""
    from .views_notes import inscrits_de
    matiere = _matiere_enseignee(request, pk)
    inscrits = list(inscrits_de(matiere).select_related('promotion'))
    if request.GET.get('imprimer') == 'pdf':
        from .pdf_listes import imprimer_tableau
        colonnes = [('N°', 22), ('Matricule', 70), ('Nom', 90), ('Prénoms', 120), ('Présence', 60), ('Émargement', 90)]
        lignes = [[str(k), i.etudiant.matricule or '', i.etudiant.nom, i.etudiant.prenom, ' ', ' ']   # cases à remplir
                  for k, i in enumerate(inscrits, start=1)]
        filtres = [('Matière', matiere.nom), ('Classe', emplois.libelle_classe(matiere)), ('Session', matiere.session),
                   ('Enseignant', str(get_profil(request).enseignant)), ('Date', '…… / …… / ………')]
        return imprimer_tableau(request, 'liste_appel', "LISTE D'APPEL", filtres, colonnes, lignes,
                                total=f"{len(lignes)} étudiant{'s' if len(lignes) > 1 else ''}",
                                vide='Aucun étudiant inscrit dans cette classe.')
    return render(request, 'pfe/enseignant_etudiants.html', {'matiere': matiere, 'inscrits': inscrits,
                                                             'actif': 'ens_etudiants'})


# ------------------------------------------------------------------ plan de cours (enseignant)

def _chapitres_postes(request):
    """Chapitres du formulaire (titre_k, heures_k, elements_k : un contenu par ligne ; vides ignorés), dans l'ordre."""
    from decimal import Decimal, InvalidOperation
    chapitres, erreurs = [], []
    nb = _entier(request.POST.get('nb_parties')) or 0
    for k in range(min(nb, plans_cours.MAX_CHAPITRES + 20)):
        titre = ' '.join((request.POST.get(f'titre_{k}') or '').split())[:255]
        elements = plans_cours._lignes(request.POST.get(f'elements_{k}') or '')[:plans_cours.MAX_ELEMENTS]
        brut = (request.POST.get(f'heures_{k}') or '').strip().replace(',', '.')
        if not titre and not elements:
            continue
        heures = None
        if brut:
            try:
                heures = Decimal(brut)
                if not 0 < heures <= 300:
                    raise InvalidOperation
            except InvalidOperation:
                erreurs.append(f"Chapitre {len(chapitres) + 1} : heures prévues invalides « {brut} ».")
        if not titre:
            erreurs.append(f"Chapitre {len(chapitres) + 1} : le titre est obligatoire.")
        chapitres.append({'titre': titre, 'heures': heures, 'elements': elements})
    return chapitres, erreurs


def _chapitres(plan):
    """Chapitres du plan avec leurs contenus et, pour chaque contenu, son état (dispensé et validé, en attente)."""
    if plan is None:
        return []
    etat = {}
    for s in plan.seances.prefetch_related('elements').select_related('validee_par').order_by('date', 'pk'):
        for e in s.elements.all():
            if s.statut == SeanceCours.VALIDEE:
                etat[e.pk] = ('fait', s)
            elif etat.get(e.pk, ('',))[0] != 'fait':
                etat[e.pk] = ('attente', s)
    chapitres = list(plan.parties.prefetch_related('elements'))
    for c in chapitres:
        c.contenus = [{'e': e, 'etat': etat.get(e.pk, ('', None))[0], 'seance': etat.get(e.pk, ('', None))[1]}
                      for e in c.elements.all()]
        c.nb_faits = sum(1 for x in c.contenus if x['etat'] == 'fait')
        c.nb_total = len(c.contenus)
        c.lignes = '\n'.join(x['e'].texte for x in c.contenus)
        c.pct = round(100 * c.nb_faits / c.nb_total) if c.nb_total else 0
    return chapitres


def _seances_du_plan(plan):
    if plan is None:
        return []
    seances = list(plan.seances.select_related('validee_par', 'enseignant', 'matiere')
                   .prefetch_related('elements__partie', 'reajustements__par'))
    for s in seances:
        s.type_court = SeanceCours.COURTS.get(s.type, s.type)
        s.duree_txt = heures.fmt(s.duree) if s.duree is not None else '—'
        s.reajustement = next(iter(s.reajustements.all()), None)
        par_chapitre = {}
        for e in s.elements.all():
            par_chapitre.setdefault(e.partie, []).append(e)
        s.par_chapitre = sorted(par_chapitre.items(), key=lambda kv: (kv[0].ordre, kv[0].pk))
    return seances


def _heure(v):
    from datetime import datetime
    try:
        return datetime.strptime((v or '').strip(), '%H:%M').time()
    except ValueError:
        return None


@login_required
@espace_enseignant_requis
def plan_cours(request, pk):
    """Plan de cours d'une de ses matières : dépôt Word, chapitres, soumission ; puis les cours dispensés."""
    matiere = _matiere_enseignee(request, pk)
    enseignant = get_profil(request).enseignant
    plan = PlanCours.objects.filter(matiere=matiere, enseignant=enseignant).first()
    erreurs = []
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'deposer':
            if plan and not plan.modifiable:
                raise PermissionDenied("Plan soumis ou approuvé : il ne se remplace plus.")
            fichier = request.FILES.get('fichier')
            proposes = []
            if not fichier or not fichier.name.lower().endswith('.docx'):
                erreurs.append("Choisissez le plan de cours au format Word (.docx).")
            elif fichier.size > 15 * 1024 * 1024:
                erreurs.append("Document trop lourd (15 Mo au plus).")
            else:
                try:
                    proposes = plans_cours.decouper_docx(fichier)
                except ValueError as e:
                    erreurs.append(str(e))
                if not erreurs:
                    with transaction.atomic():
                        plan = plan or PlanCours(matiere=matiere, enseignant=enseignant)
                        if plan.fichier:
                            stockage_prive().delete(plan.fichier.name)
                        fichier.seek(0)
                        plan.fichier.save(f"plan_{matiere.pk}_{enseignant.pk}.docx", fichier, save=False)
                        plan.nom_fichier = fichier.name[:150]
                        plan.statut = PlanCours.BROUILLON
                        plan.save()
                        plans_cours.enregistrer_chapitres(plan, proposes)
                    if proposes:
                        nb = sum(len(c['elements']) for c in proposes)
                        messages.success(request, f"Document lu : {len(proposes)} chapitre(s), {nb} contenu(s). "
                                                  f"Vérifiez et corrigez, puis soumettez le plan.")
                    else:
                        messages.warning(request, "Aucun chapitre reconnu dans le document : saisissez les chapitres "
                                                  "et leurs contenus ci-dessous.")
                    return redirect('plan_cours', pk=matiere.pk)
        elif action in ('enregistrer', 'soumettre'):
            if plan is None or not plan.modifiable:
                raise PermissionDenied("Plan soumis ou approuvé : il ne se modifie plus.")
            chapitres, erreurs = _chapitres_postes(request)
            if action == 'soumettre' and not chapitres:
                erreurs.append("Le plan doit compter au moins un chapitre.")
            if not erreurs:
                with transaction.atomic():
                    plans_cours.enregistrer_chapitres(plan, chapitres)
                    if action == 'soumettre':
                        plan.statut, plan.soumis_le = PlanCours.SOUMIS, timezone.now()
                        plan.motif_refus = ''
                    plan.save()
                messages.success(request, "Plan soumis au chef de département." if action == 'soumettre'
                                 else "Chapitres enregistrés (brouillon).")
                return redirect('plan_cours', pk=matiere.pk)
        elif action == 'declarer':
            reponse = _declarer(request, plan, erreurs)
            if reponse is not None:
                return reponse
    resigner = None
    if plan and _entier(request.GET.get('resigner')):
        resigner = plan.seances.filter(pk=_entier(request.GET.get('resigner')), statut=SeanceCours.A_RESIGNER).first()
    chapitres = _chapitres(plan)
    coches = set(request.POST.getlist('elements')) if request.method == 'POST' else set()
    for c in chapitres:                                         # après une erreur : on garde ce qui était coché
        for x in c.contenus:
            x['coche'] = str(x['e'].pk) in coches
        c.ouvert = any(x['coche'] for x in c.contenus)
    return render(request, 'pfe/plan_cours.html', {
        'matiere': matiere, 'plan': plan, 'chapitres': chapitres, 'seances': _seances_du_plan(plan),
        'avancement': plan.avancement() if plan else None, 'erreurs': erreurs, 'resigner': resigner,
        'types': SeanceCours.TYPES, 'aujourdhui': timezone.localdate(), 'post': request.POST,
        'delai': plans_cours.DELAI_DELEGUE_HEURES,
        'delegues': plans_cours.delegues_de(matiere), 'actif': 'ens_plans',
    })


def _declarer(request, plan, erreurs):
    """Cours dispensé : contenus cochés, type, date et signature de l'enseignant ; lien aux délégués (72 h)."""
    if plan is None or plan.statut != PlanCours.APPROUVE:
        raise PermissionDenied("Le plan doit d'abord être approuvé par le chef de département.")
    ids = {int(x) for x in request.POST.getlist('elements') if x.isdigit()}
    elements = list(ElementPlan.objects.filter(pk__in=ids, partie__plan=plan))
    type_cours = request.POST.get('type') if request.POST.get('type') in dict(SeanceCours.TYPES) else None
    from datetime import date as _date
    try:
        jour = _date.fromisoformat(request.POST.get('date') or '')
    except ValueError:
        jour = None
    if not elements:
        erreurs.append("Cochez les contenus que vous avez dispensés (ouvrez le chapitre).")
    if not type_cours:
        erreurs.append("Choisissez le type du cours (CM, TD, TP, contrôle…).")
    if jour is None:
        erreurs.append("Indiquez la date du cours.")
    elif jour > timezone.localdate():
        erreurs.append("La date du cours ne peut pas être dans le futur.")
    elif jour.isoweekday() == 7:
        erreurs.append("Pas de cours le dimanche : vérifiez la date.")
    png = None
    if not erreurs:
        try:
            png = preparer_signature(request.POST.get('signature', ''))
        except ValueError as e:
            erreurs.append(str(e))
    if erreurs:
        return None
    with transaction.atomic():
        s = SeanceCours(plan=plan, matiere=plan.matiere, enseignant=plan.enseignant, date=jour, type=type_cours)
        s.heure_arrivee, s.heure_depart = plans_cours.horaire_prevu(s)   # proposées au délégué, qui les ajuste
        s.save()
        s.elements.set(elements)
        plans_cours.enregistrer_signature(s, 'signature_enseignant', png, 'enseignant')
        s.save(update_fields=['signature_enseignant'])
    messages.success(request, f"Cours du {jour:%d/%m/%Y} signé ({len(elements)} contenu(s)).")
    _envoyer(request, s)
    return redirect(f"{reverse('plan_cours', args=[plan.matiere_id])}#cours")


@login_required
@espace_enseignant_requis
@require_POST
def seance_cours_action(request, pk):
    """Cours dispensé : renvoyer le lien aux délégués, supprimer (non validé), signer de nouveau (réajusté)."""
    s = get_object_or_404(SeanceCours.objects.select_related('plan', 'matiere', 'enseignant'), pk=pk)
    if s.enseignant_id != get_profil(request).enseignant.pk:
        raise Http404
    retour = f"{reverse('plan_cours', args=[s.matiere_id])}#cours"
    action = request.POST.get('action')
    if action == 'renvoyer':
        if s.statut in (SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ) and not s.expiree:
            _envoyer(request, s, garder_delai=True)
        else:
            messages.info(request, "Ce cours n'attend pas le délégué (ou le délai est dépassé : la Direction des "
                                   "Études peut le relancer).")
    elif action == 'supprimer':
        if s.statut != SeanceCours.ATTENTE or s.reajustements.exists():
            raise PermissionDenied("Un cours validé ou réajusté ne se supprime pas.")
        for f in (s.signature_enseignant, s.signature_delegue):
            if f:
                stockage_prive().delete(f.name)
        s.delete()
        messages.success(request, "Cours supprimé.")
    elif action == 'resigner':
        if s.statut != SeanceCours.A_RESIGNER:
            messages.info(request, "Ce cours n'attend pas votre signature.")
            return redirect(retour)
        try:
            png = preparer_signature(request.POST.get('signature', ''))
        except ValueError as e:
            messages.error(request, str(e))
            return redirect(f"{reverse('plan_cours', args=[s.matiere_id])}?resigner={s.pk}#signature")
        with transaction.atomic():
            plans_cours.enregistrer_signature(s, 'signature_enseignant', png, 'enseignant')
            s.statut = SeanceCours.ATTENTE_DELEGUE_REAJ
            s.save(update_fields=['signature_enseignant', 'statut'])
            s.reajustements.filter(signe_enseignant_le__isnull=True).update(signe_enseignant_le=timezone.now())
        messages.success(request, "Heures réajustées signées : un délégué doit les signer à son tour.")
        _envoyer(request, s)
    return redirect(retour)


def _envoyer(request, seance, garder_delai=False):
    expire = seance.expire_le
    try:
        adresses = plans_cours.envoyer_liens(request, seance)
    except Exception as e:                                       # SMTP indisponible…
        messages.error(request, f"Le lien n'a pas pu être envoyé aux délégués ({e}). Réessayez avec « Renvoyer ».")
        return
    if adresses and garder_delai and expire:
        seance.expire_le = expire                                # un simple renvoi ne prolonge pas le délai
        seance.save(update_fields=['expire_le'])
    if adresses:
        messages.success(request, f"Lien de validation envoyé aux délégués ({', '.join(adresses)}), à valider avant le "
                                  f"{timezone.localtime(seance.expire_le):%d/%m/%Y à %H:%M}.")
    else:
        messages.warning(request, "Aucun délégué (avec une adresse e-mail) pour cette classe : demandez au directeur du "
                                  "programme ou au chef de département de les désigner, puis « Renvoyer ».")


# ------------------------------------------------------------------ validation par un délégué (page publique)

@never_cache
def plan_confirmer(request, jeton):
    """
    Lien reçu par e-mail : le délégué vérifie les contenus du cours, ajuste l'heure d'arrivée et l'heure de départ de
    l'enseignant, saisit son matricule et signe. Après un réajustement de la Direction des Études, il signe les
    nouvelles heures (non modifiables).
    """
    ids = plans_cours.lire_jeton(jeton)
    seance = etudiant = None
    if ids:
        seance = (SeanceCours.objects.select_related('matiere__filiere', 'matiere__licence', 'matiere__session',
                                                     'enseignant', 'validee_par', 'plan')
                  .filter(pk=ids[0]).first())
        etudiant = Etudiant.objects.filter(pk=ids[1]).first()
    delegue = bool(seance and etudiant and plans_cours.est_delegue(etudiant, seance.matiere))
    reajuste = bool(seance and seance.statut == SeanceCours.ATTENTE_DELEGUE_REAJ)
    attend = bool(seance and seance.statut in (SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ))
    contexte = {'seance': seance, 'etudiant': etudiant, 'valide': bool(seance and delegue), 'erreur': '',
                'reajuste': reajuste, 'attend': attend, 'expiree': bool(seance and seance.expiree),
                'delai': plans_cours.DELAI_DELEGUE_HEURES}
    if seance and delegue:
        contexte['chapitres'] = _seances_du_plan_une(seance)
        contexte['reajustement'] = seance.reajustements.first() if reajuste else None
        contexte['arrivee'] = (request.POST.get('heure_arrivee') if request.method == 'POST' else
                               seance.heure_arrivee.strftime('%H:%M') if seance.heure_arrivee else '')
        contexte['depart'] = (request.POST.get('heure_depart') if request.method == 'POST' else
                              seance.heure_depart.strftime('%H:%M') if seance.heure_depart else '')
    if request.method == 'POST' and contexte['valide'] and attend and not contexte['expiree']:
        saisi = ''.join((request.POST.get('matricule') or '').split()).upper()
        arrivee, depart = ((seance.heure_arrivee, seance.heure_depart) if reajuste else
                           (_heure(request.POST.get('heure_arrivee')), _heure(request.POST.get('heure_depart'))))
        if not saisi or saisi.lstrip('0O') != (etudiant.matricule or '').upper().lstrip('0O'):
            contexte['erreur'] = "Matricule incorrect : saisissez votre propre matricule."
        elif not (arrivee and depart):
            contexte['erreur'] = "Indiquez l'heure d'arrivée et l'heure de départ de l'enseignant (ex. 08:00)."
        elif depart <= arrivee:
            contexte['erreur'] = "L'heure de départ doit être après l'heure d'arrivée."
        else:
            try:
                png = preparer_signature(request.POST.get('signature', ''))
            except ValueError as e:
                contexte['erreur'] = str(e)
            else:
                with transaction.atomic():
                    s = SeanceCours.objects.select_for_update().get(pk=seance.pk)
                    if s.statut in (SeanceCours.ATTENTE, SeanceCours.ATTENTE_DELEGUE_REAJ):   # l'autre délégué ?
                        plans_cours.enregistrer_signature(s, 'signature_delegue', png, f"delegue_{etudiant.pk}")
                        s.heure_arrivee, s.heure_depart = arrivee, depart
                        s.statut, s.validee_le, s.validee_par = SeanceCours.VALIDEE, timezone.now(), etudiant
                        s.signature_ip = (request.META.get('REMOTE_ADDR') or '')[:45]
                        s.save()
                        s.reajustements.filter(signe_delegue_le__isnull=True).update(signe_delegue_le=timezone.now())
                seance.refresh_from_db()
                contexte.update({'seance': seance, 'attend': False})
    return render(request, 'pfe/espace/plan_confirmer.html', contexte)


def _seances_du_plan_une(seance):
    par_chapitre = {}
    for e in seance.elements.select_related('partie'):
        par_chapitre.setdefault(e.partie, []).append(e)
    return sorted(par_chapitre.items(), key=lambda kv: (kv[0].ordre, kv[0].pk))


# ------------------------------------------------------------------ suivi : chef de département, directeur, scolarité

def _plans_visibles(profil):
    qs = PlanCours.objects.select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre',
                                          'matiere__session', 'enseignant', 'decide_par')
    if profil.est_scolarite or profil.est_direction or profil.superuser or profil.est_ciaq:
        return qs
    return qs.filter(matiere__filiere_id__in=profil.programme_ids)


def _peut_decider(profil, plan):
    return profil.approuve_plans and (profil.superuser or plan.matiere.filiere_id in profil.programme_ids)


@login_required
@plans_lecture_requise
def plans_liste(request):
    profil = get_profil(request)
    session, sessions = _session(request)
    statut = request.GET.get('statut') if request.GET.get('statut') in dict(PlanCours.STATUTS) else ''
    qs = _plans_visibles(profil).filter(matiere__session=session).prefetch_related('parties')
    if statut:
        qs = qs.filter(statut=statut)
    plans = sorted(qs, key=lambda p: (p.statut != PlanCours.SOUMIS, p.matiere.filiere.nom, p.matiere.licence.nom,
                                      p.matiere.nom))
    lignes = [{'plan': p, 'avancement': p.avancement(), 'decide': _peut_decider(profil, p)} for p in plans]
    return render(request, 'pfe/plans_liste.html', {
        'lignes': lignes, 'session': session, 'sessions': sessions, 'statut': statut, 'statuts': PlanCours.STATUTS,
        'nb_soumis': sum(1 for p in plans if p.statut == PlanCours.SOUMIS), 'actif': 'plans_cours'})


@login_required
def plan_detail(request, pk):
    """Plan, chapitres, avancement et cours dispensés ; approbation par le chef de département ou le directeur."""
    profil = get_profil(request)
    plan = get_object_or_404(PlanCours.objects.select_related(
        'matiere__filiere', 'matiere__licence', 'matiere__semestre', 'matiere__session', 'enseignant', 'decide_par'),
        pk=pk)
    proprietaire = profil.enseignant is not None and plan.enseignant_id == profil.enseignant.pk
    if not proprietaire and not _plans_visibles(profil).filter(pk=pk).exists():
        raise Http404
    if request.method == 'POST':
        if not _peut_decider(profil, plan):
            raise PermissionDenied("Seuls le chef de département et le directeur du programme approuvent ce plan.")
        if plan.statut != PlanCours.SOUMIS:
            messages.info(request, "Ce plan n'attend pas de décision.")
        elif request.POST.get('action') == 'approuver':
            plan.statut, plan.decide_par, plan.decide_le, plan.motif_refus = (PlanCours.APPROUVE, request.user,
                                                                                timezone.now(), '')
            plan.save()
            messages.success(request, "Plan de cours approuvé : l'enseignant peut déclarer ses cours.")
        elif request.POST.get('action') == 'refuser':
            motif = (request.POST.get('motif') or '').strip()
            if not motif:
                messages.error(request, "Indiquez ce que l'enseignant doit corriger.")
            else:
                plan.statut, plan.decide_par, plan.decide_le, plan.motif_refus = (PlanCours.REFUSE, request.user,
                                                                                    timezone.now(), motif[:2000])
                plan.save()
                messages.success(request, "Plan renvoyé à l'enseignant pour correction.")
        return redirect('plan_detail', pk=plan.pk)
    return render(request, 'pfe/plan_detail.html', {
        'plan': plan, 'chapitres': _chapitres(plan), 'seances': _seances_du_plan(plan),
        'avancement': plan.avancement(), 'decide': _peut_decider(profil, plan), 'proprietaire': proprietaire,
        'delegues': plans_cours.delegues_de(plan.matiere), 'reajuste': profil.reajuste_cours,
        'actif': 'ens_plans' if proprietaire and not profil.voit_plans else 'plans_cours'})


@login_required
def plan_fichier(request, pk, quoi='document', cours=None):
    """Document Word déposé, signature (enseignant, délégué) ou justificatif d'un réajustement : réservé à
    l'enseignant et au suivi."""
    profil = get_profil(request)
    plan = get_object_or_404(PlanCours, pk=pk)
    proprietaire = profil.enseignant is not None and plan.enseignant_id == profil.enseignant.pk
    if not proprietaire and not _plans_visibles(profil).filter(pk=pk).exists() and not profil.reajuste_cours:
        raise Http404
    if quoi == 'document':
        if not plan.fichier:
            raise Http404
        return FileResponse(stockage_prive().open(plan.fichier.name, 'rb'), as_attachment=True,
                            filename=plan.nom_fichier or 'plan_de_cours.docx')
    s = get_object_or_404(SeanceCours, pk=cours, plan=plan)
    if quoi == 'justificatif':
        r = s.reajustements.exclude(justificatif='').first()
        if not r:
            raise Http404
        return FileResponse(stockage_prive().open(r.justificatif.name, 'rb'), content_type='application/pdf')
    f = s.signature_enseignant if quoi == 'enseignant' else s.signature_delegue if quoi == 'delegue' else None
    if not f:
        raise Http404
    return FileResponse(stockage_prive().open(f.name, 'rb'), content_type='image/png')


# ------------------------------------------------------------------ délégués de classe

@login_required
@delegues_requis
def delegues(request):
    """Classes du périmètre (programme × niveau) de la session, avec leurs délégués (deux au plus)."""
    profil = get_profil(request)
    session, sessions = _session(request)
    programmes = Service.objects.filter(programme=True)
    if not profil.superuser:
        programmes = programmes.filter(pk__in=profil.programme_ids)
    programmes = {p.pk: p for p in programmes}
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'retirer':
            d = get_object_or_404(DelegueClasse, pk=_entier(request.POST.get('delegue')), filiere_id__in=list(programmes))
            d.delete()
            messages.success(request, f"{d.etudiant.prenom} {d.etudiant.nom} n'est plus délégué.")
        elif action == 'designer':
            insc = get_object_or_404(Inscription.objects.select_related('etudiant'),
                                     pk=_entier(request.POST.get('inscription')), session=session,
                                     filiere_id__in=list(programmes), etudiant__preinscrit=False)
            deja = DelegueClasse.objects.filter(filiere_id=insc.filiere_id, licence_id=insc.licence_id,
                                                session=session)
            if deja.count() >= DelegueClasse.MAXIMUM:
                messages.error(request, f"Cette classe a déjà {DelegueClasse.MAXIMUM} délégués : retirez-en un d'abord.")
            elif deja.filter(etudiant_id=insc.etudiant_id).exists():
                messages.info(request, "Cet étudiant est déjà délégué de cette classe.")
            else:
                DelegueClasse.objects.create(etudiant_id=insc.etudiant_id, filiere_id=insc.filiere_id,
                                             licence_id=insc.licence_id, session=session, designe_par=request.user)
                e = insc.etudiant
                avertir = '' if (e.email or '').count('@') == 1 else " Attention : il n'a pas d'adresse e-mail valide."
                messages.success(request, f"{e.prenom} {e.nom} est délégué de la classe.{avertir}")
        return redirect(f"{reverse('delegues')}?session={session.pk}" if session else reverse('delegues'))
    classes = []
    if session:
        inscriptions = (Inscription.objects.filter(session=session, filiere_id__in=list(programmes),
                                                   etudiant__preinscrit=False, conge__isnull=True)
                        .select_related('etudiant', 'licence').order_by('etudiant__nom', 'etudiant__prenom'))
        par_classe = defaultdict(list)
        for i in inscriptions:
            par_classe[(i.filiere_id, i.licence_id)].append(i)
        actuels = defaultdict(list)
        for d in DelegueClasse.objects.filter(session=session, filiere_id__in=list(programmes)).select_related(
                'etudiant', 'designe_par'):
            actuels[(d.filiere_id, d.licence_id)].append(d)
        licences = {l.pk: l for l in Licence.objects.all()}
        for (fid, lid), inscrits in par_classe.items():
            ds = actuels.get((fid, lid), [])
            pris = {d.etudiant_id for d in ds}
            classes.append({'programme': programmes[fid], 'licence': licences[lid], 'delegues': ds,
                            'candidats': [i for i in inscrits if i.etudiant_id not in pris], 'effectif': len(inscrits),
                            'complet': len(ds) >= DelegueClasse.MAXIMUM})
        classes.sort(key=lambda c: (c['programme'].nom, c['licence'].nom))
    return render(request, 'pfe/delegues.html', {'classes': classes, 'session': session, 'sessions': sessions,
                                                 'maximum': DelegueClasse.MAXIMUM, 'actif': 'delegues'})
