"""
Notes : saisie par matière, import, résultats et délibérations (soutenances : views_soutenances.py).

Droits : scolarité (tout) et chefs de programme (leurs programmes). Une fois un semestre
délibéré, seule la scolarité peut modifier ses notes, avec un motif (et un justificatif).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import ImportNotesForm
from .import_notes import OBLIGATOIRES as OBLIGATOIRES_NOTES, importer_notes, modele_notes_xlsx
from .models import (Deliberation, Etudiant, ImportNotes, Inscription, Licence, Matiere, Note, Resultat, Semestre,
                     Session, ValidationSemestre)
from .notes import (CHAMPS_SAISIE, MOTIF_SUPER_UTILISATEUR, avec_promotion, code_reglement, codes_inscriptions,
                    en_note,
                    enregistrer_notes, est_delibere,
                    filtrer_promotion, peut_modifier, recalculer, regles, saisie_de, semestres_delibres,
                    semestres_valides, session_ecoulee, verifier_pdf)
from .roles import (chef_departement_requis, chef_scolarite_requis, fiches_requises,
                    gestion_matieres_requise, get_profil, lecture_notes_requise, matieres_visibles,
                    programmes_du_profil, scolarite_requise, validations_requises)

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
         'semestre': _entier(g.get('semestre')), 'promotion': _entier(g.get('promotion')),
         'q': (g.get('q') or '').strip()}
    if len(programmes) == 1:
        f['programme'] = programmes[0].pk
    if f['programme'] and f['programme'] not in {p.pk for p in programmes}:
        f['programme'] = None
    return f, sessions, programmes


def promotions_de(f):
    """Promotions proposées : celles des inscriptions de la session et du programme choisis (+ celle choisie)."""
    from .models import Promotion
    inscriptions = Inscription.objects.filter(promotion__isnull=False)
    if f.get('session'):
        inscriptions = inscriptions.filter(session_id=f['session'])
    if f.get('programme'):
        inscriptions = inscriptions.filter(filiere_id=f['programme'])
    if f.get('licence'):
        inscriptions = inscriptions.filter(licence_id=f['licence'])
    from django.db.models.functions import Length
    return Promotion.objects.filter(Q(pk__in=inscriptions.values('promotion_id')) | Q(pk=f.get('promotion') or 0)
                                    ).order_by(Length('nom'), 'nom')


def _contexte_filtres(f, sessions, programmes):
    from .models import Licence
    return {'filtres': f, 'sessions': sessions, 'programmes': programmes, 'promotions': promotions_de(f),
            'licences': Licence.objects.order_by('id'),
            'semestres': Semestre.objects.select_related('licence').order_by('licence_id', 'id')}


def _cascade(profil, f):
    """Choix en cascade session → programme → licence → semestre → matière (saisie des notes).

    Chaque liste ne propose que ce qui existe sous les choix du dessus, parmi les matières visibles
    du profil ; un choix qui n'y figure plus (programme changé…) est vidé, ainsi que ceux du dessous.
    """
    qs = matieres_visibles(profil)
    if f.get('session'):
        qs = qs.filter(session_id=f['session'])
    programmes = list(programmes_du_profil(profil))
    licences, semestres, matieres = [], [], []
    if f.get('programme') not in {p.pk for p in programmes}:
        f['programme'] = None
    if f['programme']:
        qs = qs.filter(filiere_id=f['programme'])
        licences = list(Licence.objects.filter(pk__in=qs.values('licence_id')).order_by('id'))
    if f.get('licence') not in {l.pk for l in licences}:
        f['licence'] = None
    if f['licence']:
        qs = qs.filter(licence_id=f['licence'])
        semestres = list(Semestre.objects.filter(pk__in=qs.values('semestre_id')).order_by('id'))
    if f.get('semestre') not in {s.pk for s in semestres}:
        f['semestre'] = None
    if f['semestre']:
        matieres = list(qs.filter(semestre_id=f['semestre']).select_related('session').order_by('nom', '-session__annee'))
    return {'cascade': f, 'c_sessions': Session.objects.order_by('-annee'), 'c_programmes': programmes,
            'c_licences': licences, 'c_semestres': semestres, 'c_matieres': matieres}


def inscrits_de(matiere):
    """Étudiants inscrits (pas préinscrits) dans le programme, la session et la licence de la matière."""
    return (Inscription.objects.filter(filiere_id=matiere.filiere_id, session_id=matiere.session_id,
                                       licence_id=matiere.licence_id, etudiant__preinscrit=False, conge__isnull=True)
            .select_related('etudiant').order_by('etudiant__nom', 'etudiant__prenom'))


# ------------------------------------------------------------------ saisie

@login_required
@gestion_matieres_requise
def notes_matieres(request):
    """Choix de la matière à saisir (cascade), avec l'avancement (notes saisies / inscrits) des matières filtrées."""
    profil = get_profil(request)
    choisie = _entier(request.GET.get('matiere'))
    if choisie and matieres_visibles(profil).filter(pk=choisie).exists():     # sans JavaScript : bouton « Charger »
        return redirect('notes_saisie', pk=choisie)
    f, _, _ = _filtres(request, profil)
    cascade = _cascade(profil, f)
    qs = matieres_visibles(profil).select_related('filiere', 'licence', 'semestre', 'session')
    for champ, cle in (('session_id', 'session'), ('filiere_id', 'programme'), ('licence_id', 'licence'),
                       ('semestre_id', 'semestre')):
        if f[cle]:
            qs = qs.filter(**{champ: f[cle]})
    qs = qs.annotate(nb_notes=Count('notes', filter=Q(notes__moyenne_finale__isnull=False)))
    page = Paginator(qs.order_by('filiere__nom', 'licence_id', 'semestre_id', 'nom'), 50).get_page(request.GET.get('page'))
    matieres = list(page)
    effectifs = {}
    triplets = {(m.filiere_id, m.session_id, m.licence_id) for m in matieres}
    if triplets:
        for t in (Inscription.objects.filter(filiere_id__in={t[0] for t in triplets},
                                             session_id__in={t[1] for t in triplets},
                                             licence_id__in={t[2] for t in triplets}, etudiant__preinscrit=False,
                                             conge__isnull=True)
                  .values_list('filiere_id', 'session_id', 'licence_id').annotate(n=Count('id'))):
            effectifs[t[:3]] = t[3]
    delibres = semestres_delibres(matieres)
    valides = semestres_valides(matieres)
    for m in matieres:
        m.inscrits = effectifs.get((m.filiere_id, m.session_id, m.licence_id), 0)
        m.delibere = (m.filiere_id, m.session_id, m.semestre_id) in delibres
        m.valide = (m.filiere_id, m.session_id, m.semestre_id) in valides
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'pfe/notes_matieres.html', {
        'page': page, 'matieres': matieres, 'params': params.urlencode(), 'actif': 'notes_saisie', **cascade,
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
    inscrits = inscrits_de(matiere).select_related('promotion', 'session')
    from .models import Promotion
    from django.db.models.functions import Length
    promotions_matiere = list(Promotion.objects.filter(pk__in=inscrits.values('promotion_id'))
                              .order_by(Length('nom'), 'nom'))
    # Filtre par promotion : seuls ses étudiants sont affichés (et enregistrés)
    promotion = _entier(request.GET.get('promotion'))
    if promotion:
        inscrits = inscrits.filter(promotion_id=promotion)
    # Filtre par matricule (saisir un seul étudiant) : seuls les étudiants trouvés sont affichés et enregistrés
    matricule = (request.GET.get('matricule') or '').strip()[:25]
    if matricule:
        inscrits = inscrits.filter(etudiant__matricule__icontains=matricule)
    # Depuis la liste des notes (crayon) : seulement cet étudiant, puis retour à la liste après l'enregistrement
    seul = _entier(request.GET.get('etudiant'))
    if seul:
        inscrits = inscrits.filter(etudiant_id=seul)
    inscrits = list(inscrits)
    retour = request.GET.get('retour') or ''
    if not url_has_allowed_host_and_scheme(retour, allowed_hosts={request.get_host()}) or not retour.startswith('/'):
        retour = ''
    notes = {n.etudiant_id: n for n in Note.objects.filter(matiere=matiere)}
    erreurs, valeurs_postees, erreur_motif = {}, {}, ''

    if request.method == 'POST':
        if not modifiable:
            raise PermissionDenied("Notes fermées : semestre validé par le chef de département, délibéré ou année "
                                   "écoulée.")
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
        # Modifier une note déjà saisie : motif et justificatif PDF obligatoires (gardés dans l'historique),
        # sauf pour le super utilisateur (facultatifs ; un PDF joint doit quand même en être un)
        justificatif = request.FILES.get('justificatif')
        if profil.superuser:
            if justificatif:
                try:
                    verifier_pdf(justificatif)
                except ValueError as e:
                    erreur_motif = str(e)
            motif = motif or MOTIF_SUPER_UTILISATEUR
        elif any(note is not None for note, _, _, _ in changements):
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
            return redirect(retour or request.get_full_path())

    # Règlement de chaque étudiant (arrêté 877 : CC / CT, rattrapage plafonné) ; en-têtes : celui de tous
    codes_insc = codes_inscriptions(inscrits)
    codes = {insc.etudiant_id: codes_insc[insc.pk] for insc in inscrits}
    regle = regles(next(iter(set(codes.values())))) if len(set(codes.values())) == 1 else regles()
    libelles = {c: regle['libelles'].get(c, LIBELLES[c]) for c in CHAMPS_SAISIE}
    lignes = []
    for insc in inscrits:
        etu, note = insc.etudiant, notes.get(insc.etudiant_id)
        saisie = saisie_de(note)
        lignes.append({
            'etudiant': etu, 'note': note, 'promotion': insc.promotion, 'reglement': codes[insc.etudiant_id],
            'champs': [{'nom': c, 'libelle': libelles[c],
                        'valeur': valeurs_postees.get((etu.pk, c), '' if saisie[c] is None else _fmt(saisie[c])),
                        'erreur': erreurs.get((etu.pk, c)),
                        'calculee': _fmt(note.moyenne) if c == 'moyenne' and note and not note.moyenne_deposee
                                    and note.moyenne is not None else ''} for c in CHAMPS_SAISIE],
        })
    nb_notes = sum(1 for n in notes.values() if n.moyenne_finale is not None)
    if profil.voit_suivi_notes and notes:              # lien « historique » sur les notes modifiées
        from .models import NoteHistorique
        modifiees = set(NoteHistorique.objects.filter(note__matiere=matiere).values_list('note_id', flat=True))
        for l in lignes:
            l['modifiee'] = bool(l['note'] and l['note'].pk in modifiees)
    from .models import AutorisationNotes
    from django.utils import timezone
    aujourd_hui = timezone.localdate()
    autorisation = None
    if not profil.est_scolarite and modifiable:
        autorisation = (AutorisationNotes.objects.filter(
            programmes=matiere.filiere_id, session_id=matiere.session_id, licences=matiere.licence_id,
            date_debut__lte=aujourd_hui, date_fin__gte=aujourd_hui).order_by('-date_fin').first())
    return render(request, 'pfe/notes_saisie.html', {
        'matiere': matiere, 'lignes': lignes, 'delibere': delibere, 'modifiable': modifiable,
        'ecoulee': session_ecoulee(matiere.session), 'autorisation': autorisation,
        'promotions_matiere': promotions_matiere, 'promotion_choisie': promotion, 'matricule': matricule,
        'deliberation': Deliberation.objects.filter(filiere_id=matiere.filiere_id, session_id=matiere.session_id,
                                                    semestre_id=matiere.semestre_id).select_related('par').first(),
        'validation': ValidationSemestre.objects.filter(filiere_id=matiere.filiere_id, session_id=matiere.session_id,
                                                        semestre_id=matiere.semestre_id).select_related('par').first(),
        'erreurs': bool(erreurs), 'erreur_motif': erreur_motif, 'motif': request.POST.get('motif', ''),
        'nb_notes': nb_notes, 'regle': regle, 'libelles': libelles, 'actif': 'notes_saisie',
        'reglements': {code: regles(code) for code in set(codes.values()) | {''}},
        'reglements_melanges': len(set(codes.values())) > 1,
        'seul': seul, 'seul_etudiant': Etudiant.objects.filter(pk=seul).first() if seul else None, 'retour': retour,
        **_cascade(profil, {'session': matiere.session_id, 'programme': matiere.filiere_id,
                            'licence': matiere.licence_id, 'semestre': matiere.semestre_id, 'matiere': matiere.pk}),
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
    'promotion': ('Promotion', ['promotion_long', 'promotion_nom']),
    'finale': ('Finale', ['moyenne_finale']),
}


def _notes_filtrees(profil, f, g):
    qs = (Note.objects.filter(matiere__in=matieres_visibles(profil))
          .select_related('etudiant', 'matiere__semestre', 'matiere__licence', 'matiere__filiere', 'matiere__session'))
    for champ, cle in (('matiere__session_id', 'session'), ('matiere__filiere_id', 'programme'),
                       ('matiere__licence_id', 'licence'), ('matiere__semestre_id', 'semestre')):
        if f[cle]:
            qs = qs.filter(**{champ: f[cle]})
    if f.get('promotion'):
        qs = filtrer_promotion(qs, f['promotion'])
    qs = avec_promotion(qs)
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
@lecture_notes_requise
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
               'Session', 'Promotion', 'Contrôle', 'TP', 'Examen', 'Moyenne', 'Moyenne déposée', 'Rattrapage', 'Moyenne finale',
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
                   str(m.filiere or ''), m.session.annee, n.promotion_nom or '', n.controle, n.tp, n.examen, n.moyenne,
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
                                         pdf_enseignant=form.cleaned_data.get('pdf_enseignant'),
                                         code_fiche=form.cleaned_data.get('code_fiche', ''))
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
@fiches_requises
def notes_fiche_enseignant(request, pk):
    """Fiche de notes de l'enseignant (Excel) d'une matière : colonnes CC / CT (arrêté 877) ou contrôle / examen."""
    from django.utils.text import slugify
    from .fiche_notes import fiche_notes_xlsx
    profil = get_profil(request)
    matiere = get_object_or_404(matieres_visibles(profil).select_related('filiere', 'licence', 'semestre', 'session'),
                                pk=pk)
    inscrits = inscrits_de(matiere).select_related('session')
    promotion = _entier(request.GET.get('promotion'))
    if promotion:
        inscrits = inscrits.filter(promotion_id=promotion)
    reponse = HttpResponse(fiche_notes_xlsx(matiere, list(inscrits)),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    nom = slugify(f"fiche notes {matiere.nom} {matiere.session.annee}")[:80] or 'fiche_notes'
    reponse['Content-Disposition'] = f'attachment; filename="{nom}.xlsx"'
    return reponse


def _fichier_import(request, pk, champ):
    """
    Fichier gardé d'un import (PDF de l'enseignant ou fichier Excel importé) : la scolarité, la Direction
    Générale des Études, ou la personne qui a fait l'import. Consultation seule : jamais remplacé.
    """
    from django.http import FileResponse
    from .models import stockage_prive
    profil = get_profil(request)
    imp = get_object_or_404(ImportNotes, pk=pk)
    fichier = getattr(imp, champ)
    proprietaire = imp.utilisateur_id == request.user.id and profil.gere_matieres
    if not fichier or not (profil.voit_suivi_notes or proprietaire):
        raise Http404
    nom = fichier.name.rsplit('/', 1)[-1]
    if champ == 'pdf_enseignant':
        reponse = FileResponse(stockage_prive().open(fichier.name, 'rb'), content_type='application/pdf', filename=nom)
    else:
        types = {'.csv': 'text/csv',
                 '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
        type_ = types.get(nom[nom.rfind('.'):].lower(), 'application/octet-stream')
        reponse = FileResponse(stockage_prive().open(fichier.name, 'rb'), content_type=type_, as_attachment=True,
                               filename=nom)
    reponse['X-Content-Type-Options'] = 'nosniff'
    reponse['Cache-Control'] = 'private, no-store'
    return reponse


@login_required
def notes_import_pdf(request, pk):
    return _fichier_import(request, pk, 'pdf_enseignant')


@login_required
def notes_import_excel(request, pk):
    return _fichier_import(request, pk, 'fichier_importe')


@login_required
@gestion_matieres_requise
def notes_modele(request):
    """Modèle d'import ; ?reglement=877 : colonnes CC / CT de l'arrêté 877."""
    code = request.GET.get('reglement', '')
    code = code if code in settings.GPED_NOTES.get('reglements', {}) else ''
    reponse = HttpResponse(modele_notes_xlsx(code),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    nom = f"modele_import_notes_{code}.xlsx" if code else "modele_import_notes.xlsx"
    reponse['Content-Disposition'] = f'attachment; filename="{nom}"'
    return reponse


# ------------------------------------------------------------------ résultats et délibérations

@login_required
@lecture_notes_requise
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
        promos = dict(Inscription.objects.filter(filiere_id=f['programme'], session_id=f['session'],
                                                 licence_id=semestre.licence_id, etudiant__preinscrit=False,
                                                 conge__isnull=True)
                      .values_list('etudiant_id', 'promotion__nom'))
        inscrits = list(promos)
        if matieres and inscrits:
            recalculer(inscrits)                       # toujours à jour (nouvelle matière, note modifiée…)
        resultats = Resultat.objects.filter(niveau=Resultat.SEMESTRE, filiere_id=f['programme'],
                                            session_id=f['session'], semestre=semestre)
        if f['promotion']:                             # rangs : ceux de tout le semestre
            from .models import Promotion
            nom_promo = Promotion.objects.filter(pk=f['promotion']).values_list('nom', flat=True).first()
            inscrits = [e for e, p in promos.items() if p == nom_promo]
            resultats = resultats.filter(etudiant_id__in=inscrits)
        resultats = list(resultats.select_related('etudiant').order_by('rang', 'etudiant__nom'))
        for r in resultats:
            r.promotion_nom = promos.get(r.etudiant_id)
        moyennes = [r.moyenne for r in resultats if r.moyenne is not None]
        contexte.update({
            'semestre': semestre, 'matieres': matieres, 'resultats': resultats, 'nb_inscrits': len(inscrits),
            'credits_semestre': sum(m.credit for m in matieres),
            'nb_valides': sum(1 for m in moyennes if m >= regles()['seuil_validation']),
            'moyenne_promo': round(sum(moyennes) / len(moyennes), 2) if moyennes else None,
            'notes_manquantes': sum(r.notes_manquantes for r in resultats),
            'deliberation': Deliberation.objects.filter(filiere_id=f['programme'], session_id=f['session'],
                                                        semestre=semestre).select_related('par').first(),
            'validation': ValidationSemestre.objects.filter(filiere_id=f['programme'], session_id=f['session'],
                                                            semestre=semestre).select_related('par').first(),
            'annee_ecoulee': session_ecoulee(Session.objects.get(pk=f['session'])),
            'peut_valider': profil.est_chef_departement and f['programme'] in profil.programme_ids
                            and not session_ecoulee(Session.objects.get(pk=f['session'])),
            'programme_choisi': next((p for p in programmes if p.pk == f['programme']), None),
            'session_choisie': next((s for s in sessions if s.pk == f['session']), None),
        })
    return render(request, 'pfe/notes_resultats.html', contexte)


@login_required
@chef_scolarite_requis
@require_POST
def notes_deliberer(request):
    """Délibérer (chef de la scolarité) ou annuler une délibération (super utilisateur seulement)."""
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


@login_required
@chef_departement_requis
@require_POST
def notes_valider(request):
    """
    Le chef de département valide les notes d'un semestre de l'un de ses programmes (ou annule sa validation),
    avant la délibération : après validation, le directeur de programme ne peut plus les modifier.
    """
    profil = get_profil(request)
    filiere, session, semestre = (_entier(request.POST.get(k)) for k in ('programme', 'session', 'semestre'))
    if not (filiere and session and semestre):
        raise Http404()
    if filiere not in profil.programme_ids:
        raise PermissionDenied("Ce programme n'est pas dans votre département.")
    retour = f"{reverse('notes_resultats')}?session={session}&programme={filiere}&semestre={semestre}"
    if request.POST.get('retour') == 'validations':          # depuis la page « Validation des semestres »
        retour = f"{reverse('notes_validations')}?session={session}"
    if Deliberation.objects.filter(filiere_id=filiere, session_id=session, semestre_id=semestre).exists():
        messages.error(request, "Ce semestre est déjà délibéré : sa validation ne peut plus changer.")
        return redirect(retour)
    if session_ecoulee(get_object_or_404(Session, pk=session)):
        messages.error(request, "Année écoulée : ses notes sont considérées comme délibérées.")
        return redirect(retour)
    if request.POST.get('action') == 'annuler':
        ValidationSemestre.objects.filter(filiere_id=filiere, session_id=session, semestre_id=semestre).delete()
        messages.success(request, "Validation annulée : le directeur de programme peut de nouveau modifier les notes.")
    else:
        _, cree = ValidationSemestre.objects.get_or_create(filiere_id=filiere, session_id=session, semestre_id=semestre,
                                                           defaults={'par': request.user})
        messages.success(request, "Semestre validé : la scolarité peut le délibérer. Le directeur de programme ne "
                                  "peut plus modifier ses notes." if cree else "Ce semestre était déjà validé.")
    return redirect(retour)



# ------------------------------------------------------------------ espace de délibération (jury)

STATUTS_JURY = [('', 'Tous les étudiants'), ('tout', 'Ont tout validé'), ('pas_tout', "N'ont pas tout validé"),
                ('n', 'Ont validé exactement…')]


def grille_deliberation(profil, f):
    """
    Grille du jury : une ligne par étudiant inscrit (programme, session, licence ; promotion et recherche
    facultatives), une colonne par matière du semestre choisi, ou de toute l'année (licence) sans semestre,
    ou de la seule matière choisie. Une matière est validée à partir du seuil (5) ; sans note, elle ne l'est pas.
    Renvoie (matières, lignes) ; chaque ligne : inscription, cases, nb_validees, credits_valides, moyenne.
    """
    from .notes import arrondi, est_validee, mention
    matieres = matieres_visibles(profil).filter(session_id=f['session'], filiere_id=f['programme'],
                                                licence_id=f['licence'])
    if f.get('semestre'):
        matieres = matieres.filter(semestre_id=f['semestre'])
    if f.get('matiere'):
        matieres = matieres.filter(pk=f['matiere'])
    matieres = list(matieres.select_related('semestre').order_by('semestre_id', 'nom'))
    inscriptions = (Inscription.objects.filter(filiere_id=f['programme'], session_id=f['session'],
                                               licence_id=f['licence'], etudiant__preinscrit=False, conge__isnull=True)
                    .select_related('etudiant', 'promotion', 'session'))
    if f.get('promotion'):
        inscriptions = inscriptions.filter(promotion_id=f['promotion'])
    if f.get('q'):
        q = f['q']
        inscriptions = inscriptions.filter(Q(etudiant__matricule__icontains=q) | Q(etudiant__ine__icontains=q)
                                           | Q(etudiant__nom__icontains=q) | Q(etudiant__prenom__icontains=q))
    inscriptions = list(inscriptions.order_by('etudiant__nom', 'etudiant__prenom'))
    codes_insc = codes_inscriptions(inscriptions)
    notes = {(n.etudiant_id, n.matiere_id): n for n in
             Note.objects.filter(matiere__in=matieres, etudiant_id__in=[i.etudiant_id for i in inscriptions])}
    total_credits = sum(m.credit for m in matieres)
    lignes = []
    for insc in inscriptions:
        cases, nb, credits, points = [], 0, 0, 0
        for m in matieres:
            n = notes.get((insc.etudiant_id, m.pk))
            valeur = n.moyenne_finale if n else None
            ok = est_validee(valeur)
            nb += ok
            credits += m.credit if ok else 0
            points += (valeur or 0) * m.credit
            cases.append({'note': valeur, 'ok': ok, 'session': bool(n and n.par_session)})
        manquantes = sum(1 for c in cases if c['note'] is None)
        moyenne = arrondi(points / total_credits) if total_credits and manquantes < len(matieres) else None
        lignes.append({'inscription': insc, 'etudiant': insc.etudiant, 'cases': cases, 'nb_validees': nb,
                       'credits_valides': credits, 'manquantes': manquantes, 'moyenne': moyenne,
                       'mention': mention(moyenne, codes_insc[insc.pk])})
    return matieres, lignes


def _export_deliberation(matieres, lignes, titre):
    """Excel de la grille du jury (une colonne par matière, puis le bilan)."""
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Délibération'
    ws.append([titre])
    ws['A1'].font = Font(bold=True, size=12)
    ws.append(['N°', 'Matricule', 'INE', 'Nom', 'Prénom', 'Promotion']
              + [f"{m.nom} ({m.credit} cr.)" for m in matieres]
              + ['Matières validées', 'Crédits validés', 'Moyenne', 'Mention'])
    for cell in ws[2]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C')
    vert, rouge = PatternFill('solid', fgColor='DCFCE7'), PatternFill('solid', fgColor='FEE2E2')
    total = sum(m.credit for m in matieres)
    for k, l in enumerate(lignes, start=1):
        e, promotion = l['etudiant'], l['inscription'].promotion
        ws.append([k, e.matricule, e.ine or '', e.nom, e.prenom, promotion.nom if promotion else '']
                  + [c['note'] for c in l['cases']]
                  + [f"{l['nb_validees']} / {len(matieres)}", f"{l['credits_valides']} / {total}", l['moyenne'],
                     l['mention']])
        for j, c in enumerate(l['cases']):
            ws.cell(row=k + 2, column=7 + j).fill = vert if c['ok'] else rouge
    flux = io.BytesIO()
    wb.save(flux)
    reponse = HttpResponse(flux.getvalue(),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    reponse['Content-Disposition'] = 'attachment; filename="deliberation_geped.xlsx"'
    return reponse


@login_required
@lecture_notes_requise
def notes_deliberation(request):
    """
    Espace de délibération (à projeter devant le jury) : grille étudiants × matières, filtres (programme,
    promotion, session, licence, semestre, matière, matricule ou INE), tri des étudiants qui ont tout validé,
    pas tout validé, ou exactement N matières, et répartition par nombre de matières validées.
    """
    profil = get_profil(request)
    g = request.GET
    f, sessions, programmes = _filtres(request, profil)
    f['matiere'] = _entier(g.get('matiere'))
    cascade = _cascade(profil, f)                      # vide les choix qui n'existent plus sous ceux du dessus
    statut = g.get('statut') if g.get('statut') in {s for s, _ in STATUTS_JURY} else ''
    n_choisi = _entier(g.get('n'))
    tri = g.get('tri') if g.get('tri') in ('nom', 'moyenne', 'validees') else 'nom'
    contexte = {'actif': 'notes_deliberation', **cascade, 'filtres': f, 'statuts': STATUTS_JURY, 'statut': statut,
                'n_choisi': n_choisi, 'tri': tri, 'promotions': promotions_de(f) if f['licence'] else []}
    if f['session'] and f['programme'] and f['licence']:
        matieres, lignes = grille_deliberation(profil, f)
        nb = len(matieres)
        tous = len(lignes)
        repartition = [{'n': k, 'effectif': sum(1 for l in lignes if l['nb_validees'] == k)} for k in range(nb, -1, -1)]
        for r in repartition:
            r['pourcent'] = round(100 * r['effectif'] / tous, 1) if tous else 0
        if statut == 'tout':
            lignes = [l for l in lignes if nb and l['nb_validees'] == nb]
        elif statut == 'pas_tout':
            lignes = [l for l in lignes if l['nb_validees'] < nb]
        elif statut == 'n' and n_choisi is not None:
            lignes = [l for l in lignes if l['nb_validees'] == n_choisi]
        if tri == 'moyenne':
            lignes.sort(key=lambda l: (l['moyenne'] is None, -(l['moyenne'] or 0)))
        elif tri == 'validees':
            lignes.sort(key=lambda l: (-l['nb_validees'], -(l['moyenne'] or 0)))
        programme = next((p for p in programmes if p.pk == f['programme']), None)
        semestre = Semestre.objects.filter(pk=f['semestre']).first() if f['semestre'] else None
        licence = Licence.objects.filter(pk=f['licence']).first()
        session = next((s for s in sessions if s.pk == f['session']), None)
        portee = semestre.nom if semestre else f"Année ({licence.nom})"
        titre = ' · '.join(str(x) for x in (programme, portee, session.annee if session else '') if x)
        if g.get('export') == 'xlsx':
            return _export_deliberation(matieres, lignes, f"Délibération · {titre}")
        params = g.copy()
        for cle in ('statut', 'n', 'export', 'tri'):
            params.pop(cle, None)
        contexte.update({
            'matieres': matieres, 'lignes': lignes, 'nb_matieres': nb, 'nb_etudiants': tous,
            'repartition': repartition, 'titre_grille': titre, 'portee': portee, 'params_base': params.urlencode(),
            'credits_total': sum(m.credit for m in matieres),
            'nb_tout': repartition[0]['effectif'] if nb else 0,
            'nb_pas_tout': tous - (repartition[0]['effectif'] if nb else 0),
            'seuil': regles()['seuil_validation'],
        })
    return render(request, 'pfe/notes_deliberation.html', contexte)


# ------------------------------------------------------------------ validation des semestres (chef de département)

@login_required
@validations_requises
def notes_validations(request):
    """
    Validation des semestres : pour la session choisie (en cours par défaut), chaque semestre des programmes du
    périmètre, avec l'avancement de la saisie (notes saisies / attendues) et son état : à valider, validé par le
    chef de département, délibéré. Le chef de département valide (ou annule) d'ici ; la scolarité suit.
    """
    from collections import defaultdict
    profil = get_profil(request)
    sessions = list(Session.objects.order_by('-annee'))
    sid = _entier(request.GET.get('session'))
    session = next((s for s in sessions if s.pk == sid), None) or next((s for s in sessions if s.en_cours), None) \
        or (sessions[0] if sessions else None)
    programmes = list(programmes_du_profil(profil))
    pid = _entier(request.GET.get('programme'))
    if pid not in {p.pk for p in programmes}:
        pid = None
    choisis = [p for p in programmes if p.pk == pid] if pid else programmes
    lignes = []
    if session and choisis:
        matieres = list(Matiere.objects.filter(session=session, filiere__in=choisis)
                        .select_related('filiere', 'licence', 'semestre')
                        .annotate(nb_notes=Count('notes', filter=Q(notes__moyenne_finale__isnull=False))))
        effectifs = dict(((f, l), n) for f, l, n in
                         Inscription.objects.filter(session=session, filiere__in=choisis, etudiant__preinscrit=False,
                                                    conge__isnull=True)
                         .values_list('filiere_id', 'licence_id').annotate(n=Count('id')))
        delibres = semestres_delibres(matieres)
        validations = {(v.filiere_id, v.semestre_id): v for v in
                       ValidationSemestre.objects.filter(session=session, filiere__in=choisis).select_related('par')}
        groupes = defaultdict(list)
        for m in matieres:
            groupes[(m.filiere, m.semestre)].append(m)
        for (programme, semestre), ms in sorted(groupes.items(), key=lambda g: (g[0][0].nom, g[0][1].licence_id,
                                                                             g[0][1].pk)):
            inscrits = effectifs.get((programme.pk, ms[0].licence_id), 0)
            attendues = inscrits * len(ms)
            saisies = sum(min(m.nb_notes, inscrits) for m in ms)
            delibere = (programme.pk, session.pk, semestre.pk) in delibres
            validation = validations.get((programme.pk, semestre.pk))
            lignes.append({
                'programme': programme, 'semestre': semestre, 'licence': ms[0].licence, 'nb_matieres': len(ms),
                'credits': sum(m.credit for m in ms), 'inscrits': inscrits, 'saisies': saisies, 'attendues': attendues,
                'pourcent': round(100 * saisies / attendues) if attendues else 0,
                'etat': 'delibere' if delibere else 'valide' if validation else 'a_valider', 'validation': validation,
            })
    ecoulee = session_ecoulee(session) if session else False
    return render(request, 'pfe/notes_validations.html', {
        'actif': 'notes_validations', 'sessions': sessions, 'session': session, 'programmes': programmes,
        'programme_choisi': pid, 'lignes': lignes, 'ecoulee': ecoulee,
        'peut_valider': profil.est_chef_departement and not ecoulee,
        'nb_a_valider': sum(1 for l in lignes if l['etat'] == 'a_valider'),
        'nb_valides': sum(1 for l in lignes if l['etat'] == 'valide'),
        'nb_delibres': sum(1 for l in lignes if l['etat'] == 'delibere'),
    })
