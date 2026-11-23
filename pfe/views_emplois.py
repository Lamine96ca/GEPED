"""
Emplois du temps : grille de la semaine type (lundi → samedi, 7 h → 22 h, pas de 30 minutes).

- Vue « classe » (programme, niveau, semestre d'une session) : la Direction Générale des Études y dépose les matières,
  déplace et étire les cours, choisit le type, la salle et les enseignants (plusieurs possibles). Les conflits sont
  signalés (rouge clair) ; on peut forcer.
- Vue « département » : toutes les classes des programmes d'un département (ou de tous), une grille par classe,
  empilées ; un clic sur une grille ouvre la classe.
- Vues « enseignant » et « salle » : consultation de la semaine d'un enseignant ou de l'occupation d'une salle.
- « Heures des enseignants » : heures par semaine de chaque enseignant d'après l'emploi du temps ; au-delà du nombre
  d'heures saisi, ce sont des heures supplémentaires. Impression et export en détail (matières puis total) ou en
  résumé (total seulement).
- Impression PDF et export Excel de la grille affichée.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import emplois
from .models import Enseignant, Licence, Matiere, Salle, Seance, Semestre, Session
from .roles import (emplois_gestion_requise, emplois_lecture_requise, enseignants_visibles, get_profil,
                    matieres_visibles, programmes_du_profil)

MODES = [('classe', 'Classe'), ('departement', 'Département'), ('enseignant', 'Enseignant'), ('salle', 'Salle'),
         ('heures', 'Heures des enseignants')]
FORMATS = [('detail', 'Détail : matières puis total'), ('resume', 'Résumé : total seulement')]
PERIODES = [('impair', 'Semestres impairs (1, 3, 5…)'), ('pair', 'Semestres pairs (2, 4, 6…)')]
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _entier(v):
    return int(v) if str(v or '').lstrip('-').isdigit() else None


def _selection(request, profil):
    """Mode, session et choix de la grille, avec les listes proposées (en cascade pour la classe)."""
    g = request.GET
    sessions = list(Session.objects.order_by('-annee'))
    sid = _entier(g.get('session'))
    session = next((s for s in sessions if s.pk == sid), None) or next((s for s in sessions if s.en_cours), None) \
        or (sessions[0] if sessions else None)
    mode = g.get('vue') if g.get('vue') in dict(MODES) else 'classe'
    sel = {'mode': mode, 'session': session, 'sessions': sessions, 'periode': g.get('periode') if g.get('periode')
           in dict(PERIODES) else 'impair', 'programmes': list(programmes_du_profil(profil)), 'licences': [],
           'semestres': [], 'programme': None, 'licence': None, 'semestre': None, 'enseignant': None, 'salle': None}
    if mode == 'classe':
        # Programmes avec des matières dans la session d'abord (les autres n'ont encore rien à placer)
        avec = set(Matiere.objects.filter(session=session).values_list('filiere_id', flat=True)) if session else set()
        sel['programmes_avec'] = [p for p in sel['programmes'] if p.pk in avec]
        sel['programmes_sans'] = [p for p in sel['programmes'] if p.pk not in avec]
        sel['programme'] = next((p for p in sel['programmes'] if p.pk == _entier(g.get('programme'))), None)
        if len(sel['programmes']) == 1:
            sel['programme'] = sel['programmes'][0]
        if sel['programme'] and session:
            mats = Matiere.objects.filter(filiere=sel['programme'], session=session)
            sel['licences'] = list(Licence.objects.filter(pk__in=mats.values('licence_id')).order_by('nom'))
            if not sel['licences']:
                # Aucune matière dans cette session : on dit pourquoi, et quelles sessions en ont
                sel['sessions_avec_matieres'] = [s for s in sessions if s.pk in set(
                    Matiere.objects.filter(filiere=sel['programme']).values_list('session_id', flat=True))][:4]
            sel['licence'] = next((l for l in sel['licences'] if l.pk == _entier(g.get('licence'))), None)
            if sel['licence']:
                sel['semestres'] = list(Semestre.objects.filter(
                    pk__in=mats.filter(licence=sel['licence']).values('semestre_id')).order_by('nom'))
                sel['semestre'] = next((s for s in sel['semestres'] if s.pk == _entier(g.get('semestre'))), None)
                if sel['semestre'] is None and len(sel['semestres']) == 1:
                    sel['semestre'] = sel['semestres'][0]
    elif mode == 'heures':
        from .roles import departements_etablissement
        sel['departements'] = list(departements_etablissement())
        sel['departement'] = next((d for d in sel['departements'] if d.pk == _entier(g.get('departement'))), None)
        try:
            seuil = float(str(g.get('seuil') or '').replace(',', '.'))
            sel['seuil'] = seuil if 0 <= seuil <= 168 else None
        except ValueError:
            sel['seuil'] = None
        sel['format'] = g.get('format') if g.get('format') in dict(FORMATS) else 'detail'
    elif mode == 'departement':
        sel['departements'] = _departements_du_profil(profil)
        sel['departement'] = next((d for d in sel['departements'] if d.pk == _entier(g.get('departement'))), None)
        if len(sel['departements']) == 1:
            sel['departement'] = sel['departements'][0]
    elif mode == 'enseignant':
        sel['enseignants'] = list(enseignants_visibles(profil).filter(actif=True))
        sel['enseignant'] = next((e for e in sel['enseignants'] if e.pk == _entier(g.get('enseignant'))), None)
    else:
        sel['salles'] = list(Salle.objects.select_related('batiment'))
        sel['salle'] = next((s for s in sel['salles'] if s.pk == _entier(g.get('salle'))), None)
    return sel


def _matieres_classe(profil, sel):
    return list(matieres_visibles(profil).filter(filiere=sel['programme'], licence=sel['licence'],
                                                 semestre=sel['semestre'], session=sel['session'])
                .select_related('filiere', 'licence', 'semestre').order_by('nom'))


def _seances(profil, sel):
    """Séances de la grille choisie (ou None si la grille n'est pas encore choisie)."""
    if sel['session'] is None:
        return None
    qs = (Seance.objects.filter(matiere__session=sel['session'])
          .select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre', 'salle__batiment')
          .prefetch_related('enseignants'))
    if sel['mode'] == 'classe':
        if not sel['semestre']:
            return None
        return list(qs.filter(matiere__filiere=sel['programme'], matiere__licence=sel['licence'],
                              matiere__semestre=sel['semestre']))
    cible = sel['enseignant'] if sel['mode'] == 'enseignant' else sel['salle']
    if cible is None:
        return None
    qs = qs.filter(enseignants=cible) if sel['mode'] == 'enseignant' else qs.filter(salle=cible)
    parite = 1 if sel['periode'] == 'impair' else 0
    return [s for s in qs.distinct() if emplois.periode(s.matiere.semestre) in (parite, None)]


def _titre(sel):
    if sel['mode'] == 'classe':
        return f"{sel['programme'].nom} · {sel['licence'].nom} · {sel['semestre'].nom} · {sel['session'].annee}"
    periode = 'semestres impairs' if sel['periode'] == 'impair' else 'semestres pairs'
    if sel['mode'] == 'enseignant':
        e = sel['enseignant']
        return f"{e} · {sel['session'].annee} ({periode})"
    return f"Salle {sel['salle'].nom} ({sel['salle'].batiment.nom}) · {sel['session'].annee} ({periode})"


@login_required
@emplois_lecture_requise
def emploi_du_temps(request):
    profil = get_profil(request)
    sel = _selection(request, profil)
    seances = _seances(profil, sel)
    contexte = {'sel': sel, 'modes': MODES, 'periodes': PERIODES, 'actif': 'emplois', 'creneaux': emplois.creneaux(),
                'jours': Seance.JOURS, 'types': Seance.TYPES, 'seances': seances}
    if sel['mode'] == 'departement':
        if sel['session'] is None:
            return render(request, 'pfe/emploi_du_temps.html', contexte)
        return _vue_departement(request, profil, sel, contexte)
    if sel['mode'] == 'heures':
        contexte['formats'] = FORMATS
        if sel['session'] is None:
            return render(request, 'pfe/emploi_du_temps.html', contexte)
        return _vue_heures(request, profil, sel, contexte)
    if seances is None:
        return render(request, 'pfe/emploi_du_temps.html', contexte)
    analyses = emplois.avec_conflits(seances, sel['session'].pk)
    if request.GET.get('imprimer') == 'pdf':
        return _imprimer(request, sel, analyses)
    if request.GET.get('export') == 'xlsx':
        return _export(sel, analyses)
    peut_modifier = sel['mode'] == 'classe' and profil.gere_emplois
    donnees = {'seances': [emplois.en_json(s, m) for s, m in analyses], 'modifiable': peut_modifier,
               'mode': sel['mode'], 'debut': Seance.DEBUT_JOURNEE, 'fin': Seance.FIN_JOURNEE, 'pas': Seance.PAS,
               'jours': [lib for _, lib in Seance.JOURS], 'types': dict(Seance.TYPES)}
    if sel['mode'] == 'classe':
        matieres = _matieres_classe(profil, sel)
        donnees['matieres'] = [{'id': m.pk, 'nom': m.nom, 'code': m.code or '', 'couleur': k % 10}
                               for k, m in enumerate(matieres)]
        if peut_modifier:
            prog = sel['programme']
            donnees['departement'] = prog.departement_id or prog.pk
            donnees['effectif'] = emplois.effectif(matieres[0]) if matieres else 0
            donnees['salles'] = [{'id': s.pk, 'nom': s.nom, 'batiment': s.batiment.code or s.batiment.nom,
                                  'capacite': s.capacite, 'type': s.get_type_display()}
                                 for s in Salle.objects.filter(disponible=True).select_related('batiment')]
            donnees['enseignants'] = [{'id': e.pk, 'nom': f"{e.civilite + ' ' if e.civilite else ''}{e.prenom} {e.nom}",
                                       'departement': e.departement_id, 'nom_departement': e.departement.nom,
                                       'grade': e.get_grade_display()}
                                      for e in Enseignant.objects.filter(actif=True).select_related('departement')]
        contexte['titre_grille'] = _titre(sel)
    else:
        contexte['titre_grille'] = _titre(sel)
    contexte['donnees'] = donnees
    contexte['nb_conflits'] = sum(1 for _, m in analyses if m)
    return render(request, 'pfe/emploi_du_temps.html', contexte)


# ------------------------------------------------------------------ enregistrement (JSON)

def _erreur(message, statut=400):
    return JsonResponse({'ok': False, 'erreur': message}, status=statut)


@login_required
@emplois_gestion_requise
@require_POST
def seance_enregistrer(request, pk=None):
    """Crée ou modifie un cours. Conflit sans « forcer » : réponse 409 avec la liste des conflits."""
    profil = get_profil(request)
    try:
        d = json.loads(request.body.decode('utf-8') or '{}')
    except ValueError:
        return _erreur("Données illisibles.")
    seance = get_object_or_404(Seance.objects.select_related('matiere'), pk=pk) if pk else None
    if seance is not None:
        matiere = seance.matiere
    else:
        matiere = matieres_visibles(profil).filter(pk=_entier(d.get('matiere'))).select_related(
            'filiere', 'licence', 'semestre').first()
        if matiere is None:
            return _erreur("Matière introuvable.")
    jour, debut, fin = _entier(d.get('jour')), _entier(d.get('debut')), _entier(d.get('fin'))
    if jour not in dict(Seance.JOURS):
        return _erreur("Jour invalide.")
    if debut is None or fin is None or debut % Seance.PAS or fin % Seance.PAS \
            or not Seance.DEBUT_JOURNEE <= debut < fin <= Seance.FIN_JOURNEE:
        return _erreur("Horaire invalide : de 7 h à 22 h, par demi-heure.")
    type_ = d.get('type') if d.get('type') in dict(Seance.TYPES) else (seance.type if seance else Seance.CM)
    salle = None
    if d.get('salle'):
        salle = Salle.objects.filter(pk=_entier(d.get('salle'))).first()
        if salle is None:
            return _erreur("Salle introuvable.")
    ids = [i for i in (_entier(x) for x in d.get('enseignants') or []) if i]
    enseignants = list(Enseignant.objects.filter(pk__in=ids, actif=True))
    if len(enseignants) != len(set(ids)):
        return _erreur("Enseignant introuvable ou désactivé.")
    messages = emplois.conflits(matiere, jour, debut, fin, salle, [e.pk for e in enseignants],
                                emplois.candidates(matiere.session_id, {jour}), exclure=pk)
    if messages and not d.get('forcer'):
        return JsonResponse({'ok': False, 'conflits': [m for _, m in messages]}, status=409)
    with transaction.atomic():
        s = seance or Seance(matiere=matiere)
        s.type, s.jour, s.debut, s.fin, s.salle, s.modifie_par = type_, jour, debut, fin, salle, request.user
        s.conflit_force = bool(messages)
        s.force_par, s.force_le = (request.user, timezone.now()) if messages else (None, None)
        s.save()
        s.enseignants.set(enseignants)
    s = Seance.objects.select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre',
                                      'salle__batiment').prefetch_related('enseignants').get(pk=s.pk)
    return JsonResponse({'ok': True, 'seance': emplois.en_json(s, messages)})


@login_required
@emplois_gestion_requise
@require_POST
def seance_supprimer(request, pk):
    get_object_or_404(Seance, pk=pk).delete()
    return JsonResponse({'ok': True})


@login_required
@emplois_lecture_requise
def emploi_donnees(request):
    """Séances de la grille (pour rafraîchir les conflits après une modification)."""
    profil = get_profil(request)
    sel = _selection(request, profil)
    seances = _seances(profil, sel)
    if seances is None:
        raise Http404
    return JsonResponse({'seances': [emplois.en_json(s, m) for s, m in emplois.avec_conflits(seances, sel['session'].pk)]})


# ------------------------------------------------------------------ impression et export

def _blocs(mode, analyses):
    """Par jour, les blocs à dessiner : cours qui se chevauchent regroupés (texte de chacun, conflit éventuel)."""
    par_jour = {j: [] for j, _ in Seance.JOURS}
    for s, m in sorted(analyses, key=lambda x: (x[0].jour, x[0].debut)):
        lignes = [f"{s.matiere.nom} ({Seance.COURTS.get(s.type, s.type)})"]
        if mode not in ('classe', 'departement'):
            lignes.append(emplois.libelle_classe(s.matiere))
        if s.salle_id and mode != 'salle':
            lignes.append(f"Salle {s.salle.nom} ({s.salle.batiment.code or s.salle.batiment.nom})")
        if mode != 'enseignant':
            noms = ', '.join(emplois.nom_court(e) for e in s.enseignants.all())
            if noms:
                lignes.append(noms)
        lignes.append(f"{Seance.heure(s.debut)}–{Seance.heure(s.fin)}")
        blocs = par_jour[s.jour]
        if blocs and s.debut < blocs[-1]['fin']:
            b = blocs[-1]
            b['fin'] = max(b['fin'], s.fin)
            b['textes'].append(lignes)
            b['conflit'] = b['conflit'] or bool(m)
        else:
            blocs.append({'debut': s.debut, 'fin': s.fin, 'textes': [lignes], 'conflit': bool(m)})
    return par_jour


def _grille_pdf(mode, analyses):
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle
    creneaux = emplois.creneaux()
    jours = Seance.JOURS
    style = ParagraphStyle('cours', fontName='Helvetica', fontSize=6.3, leading=7.4)
    donnees = [['Heure'] + [lib for _, lib in jours]] + [[lib, *[''] * len(jours)] for _, lib in creneaux]
    regles = [('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
              ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#41627C')),
              ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
              ('FONTSIZE', (0, 0), (-1, -1), 6.5), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
              ('ALIGN', (0, 0), (-1, 0), 'CENTER'), ('TEXTCOLOR', (0, 1), (0, -1), colors.HexColor('#64748b'))]
    for col, (jour, _) in enumerate(jours, start=1):
        for b in _blocs(mode, analyses)[jour]:
            r0 = 1 + (b['debut'] - Seance.DEBUT_JOURNEE) // Seance.PAS
            r1 = (b['fin'] - Seance.DEBUT_JOURNEE) // Seance.PAS
            texte = '<br/><br/>'.join('<br/>'.join(f"<b>{escape(l)}</b>" if k == 0 else escape(l)
                                                    for k, l in enumerate(t)) for t in b['textes'])
            donnees[r0][col] = Paragraph(texte, style)
            regles += [('SPAN', (col, r0), (col, r1)),
                       ('BACKGROUND', (col, r0), (col, r1), colors.HexColor('#fecaca' if b['conflit'] else '#e0ecf7'))]
    largeur_jour = (842 - 60 - 45) / len(jours)
    grille = Table(donnees, colWidths=[45] + [largeur_jour] * len(jours), rowHeights=[14] + [13.4] * len(creneaux))
    grille.setStyle(TableStyle(regles))
    return grille


def _resume(analyses):
    conflits = sum(1 for _, m in analyses if m)
    return f"{len(analyses)} cours" + (f" · {conflits} en conflit (rouge clair)" if conflits else '')


def _imprimer(request, sel, analyses):
    from .pdf_listes import imprimer_sections, section
    return imprimer_sections(request, 'emploi_du_temps', 'EMPLOI DU TEMPS', [('Emploi du temps', _titre(sel))],
                             [section(avant=[_grille_pdf(sel['mode'], analyses)])], paysage=True,
                             total=_resume(analyses))


def _feuille(ws, titre, mode, analyses):
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    ws.append([titre])
    ws['A1'].font = Font(bold=True, size=12)
    ws.append(['Heure'] + [lib for _, lib in Seance.JOURS])
    for cell in ws[2]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C')
        cell.alignment = Alignment(horizontal='center')
    for _, lib in emplois.creneaux():
        ws.append([lib])
    fin_trait = Side(style='thin', color='CBD5E1')
    for col, (jour, _) in enumerate(Seance.JOURS, start=2):
        for b in _blocs(mode, analyses)[jour]:
            r0 = 3 + (b['debut'] - Seance.DEBUT_JOURNEE) // Seance.PAS
            r1 = 2 + (b['fin'] - Seance.DEBUT_JOURNEE) // Seance.PAS
            c = ws.cell(row=r0, column=col, value='\n\n'.join('\n'.join(t) for t in b['textes']))
            c.alignment = Alignment(wrap_text=True, vertical='top')
            c.fill = PatternFill('solid', fgColor='FECACA' if b['conflit'] else 'E0ECF7')
            c.border = Border(fin_trait, fin_trait, fin_trait, fin_trait)
            if r1 > r0:
                ws.merge_cells(start_row=r0, start_column=col, end_row=r1, end_column=col)
    ws.column_dimensions['A'].width = 8
    for col in 'BCDEFG':
        ws.column_dimensions[col].width = 26
    ws.freeze_panes = 'B3'


def _classeur(feuilles):
    """[(nom de l'onglet, titre, mode, analyses)] → réponse Excel (un onglet par grille)."""
    import io
    import re
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    vus = set()
    for nom, titre, mode, analyses in feuilles:
        nom = re.sub(r'[\\/*?:\[\]]', ' ', nom)[:28].strip() or 'Grille'
        base, k = nom, 2
        while nom.lower() in vus:
            nom, k = f"{base[:25]} {k}", k + 1
        vus.add(nom.lower())
        _feuille(wb.create_sheet(nom), titre, mode, analyses)
    if not wb.sheetnames:
        wb.create_sheet('Emploi du temps').append(['Aucun cours placé.'])
    flux = io.BytesIO()
    wb.save(flux)
    reponse = HttpResponse(flux.getvalue(), content_type=XLSX)
    reponse['Content-Disposition'] = 'attachment; filename="emploi_du_temps.xlsx"'
    return reponse


def _export(sel, analyses):
    return _classeur([('Emploi du temps', _titre(sel), sel['mode'], analyses)])


# ------------------------------------------------------------------ vue département (toutes les classes empilées)

def _departements_du_profil(profil):
    """Départements proposés : ceux des programmes que le profil voit (tous pour la scolarité et la Direction)."""
    from .roles import departements_etablissement
    ids = {p.departement_id or p.pk for p in programmes_du_profil(profil)}
    return [d for d in departements_etablissement() if d.pk in ids]


def _classes_departement(profil, sel):
    """[{departement, classes: [{programme, licence, semestre, analyses, dispo, titre}]}] pour la session et la
    période choisies : chaque classe qui a des matières, une grille par classe, empilées."""
    from .models import Service
    session, parite = sel['session'], 1 if sel['periode'] == 'impair' else 0
    deps = [sel['departement']] if sel['departement'] else sel['departements']
    programmes = list(programmes_du_profil(profil))
    mats = (Matiere.objects.filter(session=session, filiere__in=programmes)
            .select_related('filiere', 'licence', 'semestre'))
    classes = {}
    for m in mats:
        if emplois.periode(m.semestre) not in (parite, None):
            continue
        classes.setdefault(emplois.cle_classe(m), m)
    seances = list(Seance.objects.filter(matiere__session=session, matiere__filiere__in=programmes)
                   .select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre', 'salle__batiment')
                   .prefetch_related('enseignants'))
    analyses = emplois.avec_conflits(seances, session.pk) if seances else []
    par_classe = {}
    for s, msg in analyses:
        par_classe.setdefault(emplois.cle_classe(s.matiere), []).append((s, msg))
    groupes = []
    for d in deps:
        progs = {p.pk for p in programmes if (p.departement_id or p.pk) == d.pk}
        liste = []
        for cle, m in sorted(classes.items(), key=lambda x: (x[1].filiere.nom, x[1].licence.nom, x[1].semestre.nom)):
            if m.filiere_id not in progs:
                continue
            a = par_classe.get(cle, [])
            liste.append({'programme': m.filiere, 'licence': m.licence, 'semestre': m.semestre, 'analyses': a,
                          'dispo': emplois.disposition(a), 'nb_conflits': sum(1 for _, x in a if x),
                          'titre': f"{m.filiere.nom} · {m.licence.nom} · {m.semestre.nom}"})
        if liste:
            groupes.append({'departement': d, 'classes': liste})
    return groupes


def _vue_departement(request, profil, sel, contexte):
    periode = 'semestres impairs' if sel['periode'] == 'impair' else 'semestres pairs'
    titre = (f"{sel['departement'].nom if sel['departement'] else 'Tous les départements'} · "
             f"{sel['session'].annee} ({periode})")
    groupes = _classes_departement(profil, sel)
    toutes = [c for g in groupes for c in g['classes']]
    if request.GET.get('imprimer') == 'pdf':
        from .pdf_listes import imprimer_sections, section
        sections = [section(titre=f"{g['departement'].nom} — {c['titre']}", avant=[_grille_pdf('departement',
                    c['analyses'])], note=_resume(c['analyses'])) for g in groupes for c in g['classes']]
        return imprimer_sections(request, 'emplois_du_temps_departement', 'EMPLOIS DU TEMPS',
                                 [('Département', titre)], sections or [section(note='Aucune classe.')],
                                 paysage=True, total=f"{len(toutes)} classe{'s' if len(toutes) > 1 else ''}")
    if request.GET.get('export') == 'xlsx':
        return _classeur([(f"{c['programme'].nom[:14]} {c['licence'].nom[-2:].strip()} {c['semestre'].nom[-2:].strip()}",
                           f"{g['departement'].nom} — {c['titre']} · {sel['session'].annee}", 'departement',
                           c['analyses']) for g in groupes for c in g['classes']])
    contexte.update({'groupes': groupes, 'titre_grille': titre, 'nb_classes': len(toutes),
                     'nb_conflits': sum(c['nb_conflits'] for c in toutes)})
    return render(request, 'pfe/emploi_du_temps.html', contexte)


# ------------------------------------------------------------------ heures des enseignants

def fmt_heures(h):
    """4.5 → « 4 h 30 » ; 6 → « 6 h »."""
    minutes = round(h * 60)
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes % 60 else f"{minutes // 60} h"


def heures_enseignants(profil, sel):
    """Par enseignant (visible, actif) : ses cours de la semaine type (session, période), regroupés par matière,
    classe et type, le total d'heures par semaine et les heures au-delà du seuil. Un cours à plusieurs
    enseignants compte entièrement pour chacun (ils y sont tous)."""
    parite = 1 if sel['periode'] == 'impair' else 0
    enseignants = enseignants_visibles(profil).filter(actif=True).select_related('departement')
    if sel['departement']:
        enseignants = enseignants.filter(departement=sel['departement'])
    enseignants = {e.pk: e for e in enseignants}
    seances = (Seance.objects.filter(matiere__session=sel['session'], enseignants__in=list(enseignants))
               .select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre')
               .prefetch_related('enseignants').distinct())
    charges = {}
    for s in seances:
        if emplois.periode(s.matiere.semestre) not in (parite, None):
            continue
        duree = (s.fin - s.debut) / 60
        for e in s.enseignants.all():
            if e.pk not in enseignants:
                continue
            lignes = charges.setdefault(e.pk, {})
            cle = (s.matiere_id, s.type)
            if cle not in lignes:
                lignes[cle] = {'matiere': s.matiere.nom, 'code': s.matiere.code or '', 'type': Seance.COURTS.get(s.type, s.type),
                               'classe': emplois.libelle_classe(s.matiere), 'heures': 0, 'cours': 0}
            lignes[cle]['heures'] += duree
            lignes[cle]['cours'] += 1
    resultat = []
    for pk, lignes in charges.items():
        e = enseignants[pk]
        detail = sorted(lignes.values(), key=lambda l: (l['matiere'], l['classe'], l['type']))
        for l in detail:
            l['heures_txt'] = fmt_heures(l['heures'])
        total = sum(l['heures'] for l in detail)
        sup = max(0, total - sel['seuil']) if sel['seuil'] is not None else 0
        resultat.append({'enseignant': e, 'lignes': detail, 'total': total, 'total_txt': fmt_heures(total),
                         'sup': sup, 'sup_txt': fmt_heures(sup) if sup else ''})
    resultat.sort(key=lambda r: (-r['total'], r['enseignant'].nom, r['enseignant'].prenom))
    return resultat


def _titre_heures(sel):
    periode = 'semestres impairs' if sel['periode'] == 'impair' else 'semestres pairs'
    return (f"{sel['departement'].nom if sel['departement'] else 'Tous les départements'} · "
            f"{sel['session'].annee} ({periode})")


def _vue_heures(request, profil, sel, contexte):
    lignes = heures_enseignants(profil, sel)
    fmt = request.GET.get('format') if request.GET.get('format') in dict(FORMATS) else sel['format']
    if request.GET.get('imprimer') == 'pdf':
        return _imprimer_heures(request, sel, lignes, fmt)
    if request.GET.get('export') == 'xlsx':
        return _export_heures(sel, lignes, fmt)
    contexte.update({'heures': lignes, 'titre_grille': _titre_heures(sel),
                     'nb_sup': sum(1 for r in lignes if r['sup']),
                     'total_sup_txt': fmt_heures(sum(r['sup'] for r in lignes))})
    return render(request, 'pfe/emploi_du_temps.html', contexte)


def _nom_complet(e):
    return ' '.join(x for x in (e.civilite, e.prenom, e.nom) if x)


def _imprimer_heures(request, sel, lignes, fmt):
    from reportlab.lib import colors
    from .pdf_listes import cellule, imprimer_tableau
    orange = colors.HexColor('#FDE68A')
    filtres = [('Heures des enseignants', _titre_heures(sel)),
               ('Au-delà de', fmt_heures(sel['seuil']) + ' par semaine' if sel['seuil'] is not None else None)]
    nb_sup = sum(1 for r in lignes if r['sup'])
    total = (f"{len(lignes)} enseignant{'s' if len(lignes) > 1 else ''} · heures par semaine (semaine type)"
             + (f" · {nb_sup} avec des heures supplémentaires" if sel['seuil'] is not None else ''))
    if fmt == 'resume':
        colonnes = [('N°', 18), ('Enseignant', 150), ('Département', 140), ('Statut', 60), ('Heures / semaine', 60),
                    ('Heures sup.', 60)]
        rangs = [[cellule(k, align='c'), cellule(_nom_complet(r['enseignant']), r['enseignant'].get_grade_display()),
                  r['enseignant'].departement.nom, r['enseignant'].get_statut_display(),
                  cellule(r['total_txt'], align='c', gras=True),
                  cellule(r['sup_txt'] or '—', align='c', gras=bool(r['sup']), fond=orange if r['sup'] else None)]
                 for k, r in enumerate(lignes, start=1)]
    else:
        colonnes = [('Enseignant', 130), ('Matière', 140), ('Classe', 150), ('Type', 45), ('Séances / sem.', 42),
                    ('Heures', 50), ('Heures sup.', 50)]
        rangs = []
        for r in lignes:
            e = r['enseignant']
            for k, l in enumerate(r['lignes']):
                rangs.append([cellule(_nom_complet(e), e.departement.nom) if k == 0 else '',
                              cellule(l['matiere'], l['code']), l['classe'], cellule(l['type'], align='c'),
                              cellule(l['cours'], align='c'), cellule(l['heures_txt'], align='c'), ''])
            fond = orange if r['sup'] else colors.HexColor('#E5EAF0')
            rangs.append([cellule(f"Total {e.prenom} {e.nom}", gras=True, fond=fond), cellule('', fond=fond),
                          cellule('', fond=fond), cellule('', fond=fond), cellule('', fond=fond),
                          cellule(r['total_txt'], align='c', gras=True, fond=fond),
                          cellule(r['sup_txt'] or '—', align='c', gras=True, fond=fond)])
    return imprimer_tableau(request, 'heures_enseignants', 'HEURES DES ENSEIGNANTS', filtres, colonnes, rangs,
                            paysage=fmt != 'resume', total=total, vide='Aucun enseignant avec des cours placés.')


def _export_heures(sel, lignes, fmt):
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Heures' if fmt == 'resume' else 'Heures (détail)'
    ws.append([f"Heures des enseignants · {_titre_heures(sel)}"
               + (f" · au-delà de {fmt_heures(sel['seuil'])} par semaine" if sel['seuil'] is not None else '')])
    ws['A1'].font = Font(bold=True, size=12)
    bleu, orange, gris = (PatternFill('solid', fgColor='41627C'), PatternFill('solid', fgColor='FDE68A'),
                          PatternFill('solid', fgColor='E5EAF0'))
    if fmt == 'resume':
        ws.append(['N°', 'Enseignant', 'Grade', 'Département', 'Statut', 'Heures / semaine', 'Heures sup.'])
        for k, r in enumerate(lignes, start=1):
            e = r['enseignant']
            ws.append([k, _nom_complet(e), e.get_grade_display(), e.departement.nom, e.get_statut_display(),
                       round(r['total'], 2), round(r['sup'], 2) if r['sup'] else None])
            if r['sup']:
                ws.cell(row=ws.max_row, column=7).fill = orange
        largeurs = (5, 30, 22, 34, 12, 16, 12)
    else:
        ws.append(['Enseignant', 'Département', 'Matière', 'Code', 'Classe', 'Type', 'Séances / semaine', 'Heures / semaine',
                   'Heures sup.'])
        for r in lignes:
            e = r['enseignant']
            for k, l in enumerate(r['lignes']):
                ws.append([_nom_complet(e) if k == 0 else '', e.departement.nom if k == 0 else '', l['matiere'],
                           l['code'], l['classe'], l['type'], l['cours'], round(l['heures'], 2), None])
            ws.append([f"Total {e.prenom} {e.nom}", '', '', '', '', '', '', round(r['total'], 2),
                       round(r['sup'], 2) if r['sup'] else None])
            for cell in ws[ws.max_row]:
                cell.font, cell.fill = Font(bold=True), orange if r['sup'] else gris
        largeurs = (28, 28, 30, 10, 40, 10, 17, 16, 12)
    for cell in ws[2]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), bleu
    for k, w in enumerate(largeurs):
        ws.column_dimensions[openpyxl.utils.get_column_letter(k + 1)].width = w
    ws.freeze_panes = 'A3'
    flux = io.BytesIO()
    wb.save(flux)
    reponse = HttpResponse(flux.getvalue(), content_type=XLSX)
    reponse['Content-Disposition'] = 'attachment; filename="heures_enseignants.xlsx"'
    return reponse
