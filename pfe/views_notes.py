"""
Notes : saisie par matière, import, résultats et délibérations (soutenances : views_soutenances.py).

Droits : scolarité (tout) et chefs de programme (leurs programmes). Une fois un semestre
délibéré, seule la scolarité peut modifier ses notes, avec un motif (et un justificatif).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import ImportNotesForm
from .import_notes import OBLIGATOIRES as OBLIGATOIRES_NOTES, importer_notes, modele_notes_xlsx
from .models import Deliberation, ImportNotes, Inscription, Matiere, Note, Resultat, Semestre, Session
from .notes import (CHAMPS_SAISIE, en_note, enregistrer_notes, est_delibere, peut_modifier, recalculer, regles,
                    saisie_de, semestres_delibres, session_ecoulee, verifier_pdf)
from .roles import gestion_matieres_requise, get_profil, matieres_visibles, programmes_du_profil, scolarite_requise

LIBELLES = {'controle': 'Contrôle', 'tp': 'TP', 'examen': 'Examen', 'moyenne': 'Moyenne', 'rattrapage': 'Rattrapage'}


def _entier(valeur):
    return int(valeur) if str(valeur or '').isdigit() else None


def _filtres(request, profil):
    """Session (en cours par défaut), programme (imposé s'il n'y en a qu'un), licence, semestre, recherche."""
    g = request.GET
    sessions = Session.objects.order_by('-annee')
    if 'session' in g:
        session_id = _entier(g.get('session'))
    else:
        courante = sessions.filter(en_cours=True).first() or sessions.first()
        session_id = courante.pk if courante else None
    programmes = list(programmes_du_profil(profil))
    f = {'session': session_id, 'programme': _entier(g.get('programme')), 'licence': _entier(g.get('licence')),
         'semestre': _entier(g.get('semestre')), 'q': (g.get('q') or '').strip()}
    if len(programmes) == 1:
        f['programme'] = programmes[0].pk
    if f['programme'] and f['programme'] not in {p.pk for p in programmes}:
        f['programme'] = None
    return f, sessions, programmes


def _contexte_filtres(f, sessions, programmes):
    from .models import Licence
    return {'filtres': f, 'sessions': sessions, 'programmes': programmes,
            'licences': Licence.objects.order_by('id'),
            'semestres': Semestre.objects.select_related('licence').order_by('licence_id', 'id')}


def inscrits_de(matiere):
    """Étudiants inscrits (pas préinscrits) dans le programme, la session et la licence de la matière."""
    return (Inscription.objects.filter(filiere_id=matiere.filiere_id, session_id=matiere.session_id,
                                       licence_id=matiere.licence_id, etudiant__preinscrit=False)
            .select_related('etudiant').order_by('etudiant__nom', 'etudiant__prenom'))


# ------------------------------------------------------------------ saisie

@login_required
@gestion_matieres_requise
def notes_matieres(request):
    """Choix de la matière à saisir, avec l'avancement (notes saisies / inscrits)."""
    profil = get_profil(request)
    f, sessions, programmes = _filtres(request, profil)
    qs = matieres_visibles(profil).select_related('filiere', 'licence', 'semestre', 'session')
    for champ, cle in (('session_id', 'session'), ('filiere_id', 'programme'), ('licence_id', 'licence'),
                       ('semestre_id', 'semestre')):
        if f[cle]:
            qs = qs.filter(**{champ: f[cle]})
    if f['q']:
        qs = qs.filter(Q(nom__icontains=f['q']) | Q(code__icontains=f['q']))
    qs = qs.annotate(nb_notes=Count('notes', filter=Q(notes__moyenne_finale__isnull=False)))
    page = Paginator(qs.order_by('filiere__nom', 'licence_id', 'semestre_id', 'nom'), 50).get_page(request.GET.get('page'))
    matieres = list(page)
    effectifs = {}
    triplets = {(m.filiere_id, m.session_id, m.licence_id) for m in matieres}
    if triplets:
        for t in (Inscription.objects.filter(filiere_id__in={t[0] for t in triplets},
                                             session_id__in={t[1] for t in triplets},
                                             licence_id__in={t[2] for t in triplets}, etudiant__preinscrit=False)
                  .values_list('filiere_id', 'session_id', 'licence_id').annotate(n=Count('id'))):
            effectifs[t[:3]] = t[3]
    delibres = semestres_delibres(matieres)
    for m in matieres:
        m.inscrits = effectifs.get((m.filiere_id, m.session_id, m.licence_id), 0)
        m.delibere = (m.filiere_id, m.session_id, m.semestre_id) in delibres
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'pfe/notes_matieres.html', {
        'page': page, 'matieres': matieres, 'params': params.urlencode(), 'actif': 'notes_saisie',
        **_contexte_filtres(f, sessions, programmes),
    })


@login_required
@gestion_matieres_requise
def notes_saisie(request, pk):
    """Grille de saisie : une ligne par étudiant inscrit, enregistrée en une fois."""
    profil = get_profil(request)
    matiere = get_object_or_404(matieres_visibles(profil).select_related('filiere', 'licence', 'semestre', 'session'),
                                pk=pk)
    delibere = est_delibere(matiere)
    modifiable = peut_modifier(profil, matiere, delibere)
    inscrits = list(inscrits_de(matiere))
    notes = {n.etudiant_id: n for n in Note.objects.filter(matiere=matiere)}
    erreurs, valeurs_postees, erreur_motif = {}, {}, ''

    if request.method == 'POST':
        if not modifiable:
            raise PermissionDenied("Année écoulée : seul un chef (scolarité ou département) ou un super utilisateur "
                                   "peut modifier ces notes.")
        motif = (request.POST.get('motif') or '').strip()
        changements = []
        for insc in inscrits:
            etu = insc.etudiant_id
            valeurs = {}
            for c in CHAMPS_SAISIE:
                brut = request.POST.get(f'{c}_{etu}', '')
                valeurs_postees[(etu, c)] = brut
                try:
                    valeurs[c] = en_note(brut)
                except ValueError as e:
                    erreurs[(etu, c)] = str(e)
            if any((etu, c) in erreurs for c in CHAMPS_SAISIE):
                continue
            note = notes.get(etu)
            if note is None and all(v is None for v in valeurs.values()):
                continue
            if note is not None and saisie_de(note) == valeurs:
                continue
            changements.append((note, etu, matiere, valeurs))
        # Modifier une note déjà saisie : motif et justificatif PDF obligatoires (gardés dans l'historique)
        justificatif = request.FILES.get('justificatif')
        if any(note is not None for note, _, _, _ in changements):
            if not motif:
                erreur_motif = "Vous modifiez des notes déjà saisies : indiquez le motif et joignez le justificatif PDF."
            else:
                try:
                    verifier_pdf(justificatif)
                except ValueError as e:
                    erreur_motif = f"Vous modifiez des notes déjà saisies : {e}"
        if not erreurs and not erreur_motif:
            with transaction.atomic():
                creees, modifiees = enregistrer_notes(changements, request.user, apres_deliberation=delibere,
                                                      motif=motif, justificatif=justificatif)
                recalculer({etu for _, etu, _, _ in changements})
            if creees or modifiees:
                messages.success(request, f"Notes enregistrées : {creees} nouvelle(s), {modifiees} modifiée(s).")
            else:
                messages.info(request, "Aucune modification.")
            return redirect('notes_saisie', pk=matiere.pk)

    lignes = []
    for insc in inscrits:
        etu, note = insc.etudiant, notes.get(insc.etudiant_id)
        saisie = saisie_de(note)
        lignes.append({
            'etudiant': etu, 'note': note,
            'champs': [{'nom': c, 'libelle': LIBELLES[c],
                        'valeur': valeurs_postees.get((etu.pk, c), '' if saisie[c] is None else _fmt(saisie[c])),
                        'erreur': erreurs.get((etu.pk, c)),
                        'calculee': _fmt(note.moyenne) if c == 'moyenne' and note and not note.moyenne_deposee
                                    and note.moyenne is not None else ''} for c in CHAMPS_SAISIE],
        })
    nb_notes = sum(1 for n in notes.values() if n.moyenne_finale is not None)
    return render(request, 'pfe/notes_saisie.html', {
        'matiere': matiere, 'lignes': lignes, 'delibere': delibere, 'modifiable': modifiable,
        'ecoulee': session_ecoulee(matiere.session),
        'deliberation': Deliberation.objects.filter(filiere_id=matiere.filiere_id, session_id=matiere.session_id,
                                                    semestre_id=matiere.semestre_id).select_related('par').first(),
        'erreurs': bool(erreurs), 'erreur_motif': erreur_motif, 'motif': request.POST.get('motif', ''),
        'nb_notes': nb_notes, 'regles': regles(), 'actif': 'notes_saisie',
    })


def _fmt(v):
    """6.0 → « 6 », 6.25 → « 6.25 » (champ de saisie)."""
    return ('%g' % v) if v is not None else ''


# ------------------------------------------------------------------ liste des notes

STATUTS_NOTE = [('validee', 'Validées (≥ 5)'), ('non_validee', 'Non validées (< 5)'), ('session', 'Validées en session'),
                ('incomplete', 'Sans moyenne')]

# Colonnes triables : clé → (titre, champs de tri)
TRIS_NOTES = {
    'etudiant': ('Étudiant', ['etudiant__nom', 'etudiant__prenom']),
    'matiere': ('Matière', ['matiere__nom']),
    'semestre': ('Semestre', ['matiere__semestre_id']),
    'programme': ('Programme', ['matiere__filiere__nom']),
    'session': ('Session', ['matiere__session__annee']),
    'finale': ('Finale', ['moyenne_finale']),
}


def _notes_filtrees(profil, f, g):
    qs = (Note.objects.filter(matiere__in=matieres_visibles(profil))
          .select_related('etudiant', 'matiere__semestre', 'matiere__licence', 'matiere__filiere', 'matiere__session'))
    for champ, cle in (('matiere__session_id', 'session'), ('matiere__filiere_id', 'programme'),
                       ('matiere__licence_id', 'licence'), ('matiere__semestre_id', 'semestre')):
        if f[cle]:
            qs = qs.filter(**{champ: f[cle]})
    if f['q']:
        q = f['q']
        qs = qs.filter(Q(etudiant__matricule__icontains=q) | Q(etudiant__nom__icontains=q)
                       | Q(etudiant__prenom__icontains=q) | Q(matiere__nom__icontains=q) | Q(matiere__code__icontains=q))
    seuil = regles()['seuil_validation']
    statut = g.get('statut')
    if statut == 'validee':
        qs = qs.filter(moyenne_finale__gte=seuil)
    elif statut == 'non_validee':
        qs = qs.filter(moyenne_finale__lt=seuil)
    elif statut == 'session':
        qs = qs.filter(par_session=True, moyenne_finale__gte=seuil)
    elif statut == 'incomplete':
        qs = qs.filter(moyenne_finale__isnull=True)
    f['statut'] = statut if statut in dict(STATUTS_NOTE) else ''
    tri = g.get('tri') if g.get('tri') in TRIS_NOTES else None
    ordre = 'desc' if g.get('ordre') == 'desc' else 'asc'
    if tri:
        champs = [('-' if ordre == 'desc' else '') + c for c in TRIS_NOTES[tri][1]]
        qs = qs.order_by(*champs, 'etudiant__nom', 'pk')
    else:
        qs = qs.order_by('matiere__filiere__nom', 'matiere__semestre_id', 'matiere__nom', 'etudiant__nom',
                         'etudiant__prenom', 'pk')
    return qs, tri, ordre


@login_required
@gestion_matieres_requise
def notes_liste(request):
    """Toutes les notes du périmètre (scolarité : toutes ; chef de programme : ses programmes)."""
    profil = get_profil(request)
    f, sessions, programmes = _filtres(request, profil)
    qs, tri, ordre = _notes_filtrees(profil, f, request.GET)
    if request.GET.get('export') == 'xlsx':
        return _export_notes(qs)
    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    params = request.GET.copy()
    for cle_ in ('page', 'export'):
        params.pop(cle_, None)
    sans_tri = params.copy()
    for cle_ in ('tri', 'ordre'):
        sans_tri.pop(cle_, None)
    colonnes = {}
    for cle_, (libelle, _) in TRIS_NOTES.items():
        lien = sans_tri.copy()
        lien['tri'], lien['ordre'] = cle_, ('desc' if cle_ == tri and ordre == 'asc' else 'asc')
        colonnes[cle_] = {'libelle': libelle, 'actif': cle_ == tri, 'ordre': ordre, 'url': lien.urlencode()}
    return render(request, 'pfe/notes_liste.html', {
        'page': page, 'params': params.urlencode(), 'colonnes': colonnes, 'tri': tri, 'ordre': ordre,
        'statuts': STATUTS_NOTE, 'seuil': regles()['seuil_validation'], 'actif': 'notes_liste',
        **_contexte_filtres(f, sessions, programmes),
    })


def _export_notes(qs):
    """Excel des notes filtrées (mode « write_only » : rapide même pour des dizaines de milliers de lignes)."""
    import io
    import openpyxl
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet('Notes')
    entetes = ['Matricule', 'Nom', 'Prénom', 'Matière', 'Code', 'Crédits', 'Semestre', 'Licence', 'Programme',
               'Session', 'Contrôle', 'TP', 'Examen', 'Moyenne', 'Moyenne déposée', 'Rattrapage', 'Moyenne finale',
               'Mention', 'Validée en session', 'Validée']
    style = (Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C'))
    ligne = []
    for titre in entetes:
        cell = WriteOnlyCell(ws, value=titre)
        cell.font, cell.fill = style
        ligne.append(cell)
    ws.append(ligne)
    for n in qs.iterator(chunk_size=2000):
        e, m = n.etudiant, n.matiere
        ws.append([e.matricule, e.nom, e.prenom, m.nom, m.code, m.credit, m.semestre.nom, m.licence.nom,
                   str(m.filiere or ''), m.session.annee, n.controle, n.tp, n.examen, n.moyenne,
                   'oui' if n.moyenne_deposee else 'non', n.rattrapage, n.moyenne_finale, n.mention,
                   'oui' if n.par_session else 'non', 'oui' if n.validee else 'non'])
    flux = io.BytesIO()
    wb.save(flux)
    reponse = HttpResponse(flux.getvalue(),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    reponse['Content-Disposition'] = 'attachment; filename="notes_geped.xlsx"'
    return reponse


# ------------------------------------------------------------------ import

@login_required
@gestion_matieres_requise
def notes_importer(request):
    profil = get_profil(request)
    rapport = None
    if request.method == 'POST':
        form = ImportNotesForm(request.POST, request.FILES, profil=profil)
        if form.is_valid():
            try:
                rapport = importer_notes(form.cleaned_data['fichier'], profil, request.user,
                                         simulation=form.cleaned_data['simulation'],
                                         pdf_enseignant=form.cleaned_data.get('pdf_enseignant'))
            except ValueError as e:
                form.add_error('fichier', str(e))
            else:
                if not rapport.simulation and (rapport.creees or rapport.mises_a_jour):
                    messages.success(request, "Import des notes terminé et enregistré.")
    else:
        form = ImportNotesForm(profil=profil)
    journal = ImportNotes.objects.select_related('utilisateur')
    if not profil.est_scolarite:
        journal = journal.filter(utilisateur=request.user)
    return render(request, 'pfe/notes_importer.html', {
        'form': form, 'rapport': rapport, 'obligatoires': OBLIGATOIRES_NOTES, 'actif': 'notes_importer',
        'mes_programmes': None if profil.est_scolarite else programmes_du_profil(profil),
        'journal': journal[:15],
    })


@login_required
@gestion_matieres_requise
def notes_import_pdf(request, pk):
    """PDF de l'enseignant d'un import : la scolarité, ou la personne qui a fait l'import."""
    from django.http import FileResponse
    profil = get_profil(request)
    imp = get_object_or_404(ImportNotes, pk=pk)
    if not imp.pdf_enseignant or not (profil.est_scolarite or imp.utilisateur_id == request.user.id):
        raise Http404
    from .models import stockage_prive
    return FileResponse(stockage_prive().open(imp.pdf_enseignant.name, 'rb'), content_type='application/pdf',
                        filename=imp.pdf_enseignant.name.rsplit('/', 1)[-1])


@login_required
@gestion_matieres_requise
def notes_modele(request):
    reponse = HttpResponse(modele_notes_xlsx(),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    reponse['Content-Disposition'] = 'attachment; filename="modele_import_notes.xlsx"'
    return reponse


# ------------------------------------------------------------------ résultats et délibérations

@login_required
@gestion_matieres_requise
def notes_resultats(request):
    """Résultats d'un semestre (programme + session + semestre) et sa délibération."""
    profil = get_profil(request)
    f, sessions, programmes = _filtres(request, profil)
    contexte = {'actif': 'notes_resultats', **_contexte_filtres(f, sessions, programmes)}
    if f['session'] and f['programme'] and f['semestre']:
        semestre = get_object_or_404(Semestre.objects.select_related('licence'), pk=f['semestre'])
        matieres = list(Matiere.objects.filter(filiere_id=f['programme'], session_id=f['session'], semestre=semestre)
                        .annotate(nb_notes=Count('notes', filter=Q(notes__moyenne_finale__isnull=False)))
                        .order_by('nom'))
        inscrits = list(Inscription.objects.filter(filiere_id=f['programme'], session_id=f['session'],
                                                   licence_id=semestre.licence_id, etudiant__preinscrit=False)
                        .values_list('etudiant_id', flat=True))
        if matieres and inscrits:
            recalculer(inscrits)                       # toujours à jour (nouvelle matière, note modifiée…)
        resultats = list(Resultat.objects.filter(niveau=Resultat.SEMESTRE, filiere_id=f['programme'],
                                                 session_id=f['session'], semestre=semestre)
                         .select_related('etudiant').order_by('rang', 'etudiant__nom'))
        moyennes = [r.moyenne for r in resultats if r.moyenne is not None]
        contexte.update({
            'semestre': semestre, 'matieres': matieres, 'resultats': resultats, 'nb_inscrits': len(inscrits),
            'credits_semestre': sum(m.credit for m in matieres),
            'nb_valides': sum(1 for m in moyennes if m >= regles()['seuil_validation']),
            'moyenne_promo': round(sum(moyennes) / len(moyennes), 2) if moyennes else None,
            'notes_manquantes': sum(r.notes_manquantes for r in resultats),
            'deliberation': Deliberation.objects.filter(filiere_id=f['programme'], session_id=f['session'],
                                                        semestre=semestre).select_related('par').first(),
            'programme_choisi': next((p for p in programmes if p.pk == f['programme']), None),
            'session_choisie': next((s for s in sessions if s.pk == f['session']), None),
        })
    return render(request, 'pfe/notes_resultats.html', contexte)


@login_required
@scolarite_requise
@require_POST
def notes_deliberer(request):
    """Délibérer (scolarité) ou annuler une délibération (super utilisateur seulement)."""
    filiere, session, semestre = (_entier(request.POST.get(k)) for k in ('programme', 'session', 'semestre'))
    if not (filiere and session and semestre):
        raise Http404()
    retour = f"{reverse('notes_resultats')}?session={session}&programme={filiere}&semestre={semestre}"
    if request.POST.get('action') == 'annuler':
        if not request.user.is_superuser:
            raise PermissionDenied("Seul un super utilisateur peut annuler une délibération.")
        Deliberation.objects.filter(filiere_id=filiere, session_id=session, semestre_id=semestre).delete()
        messages.success(request, "Délibération annulée : les notes du semestre sont de nouveau modifiables.")
    else:
        _, cree = Deliberation.objects.get_or_create(filiere_id=filiere, session_id=session, semestre_id=semestre,
                                                     defaults={'par': request.user})
        messages.success(request, "Semestre délibéré : ses notes sont verrouillées (modifiables seulement par la "
                                  "scolarité, avec un motif)." if cree else "Ce semestre était déjà délibéré.")
    return redirect(retour)

