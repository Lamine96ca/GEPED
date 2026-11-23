"""
Emplois du temps : grille de la semaine type (lundi → samedi, 7 h → 22 h, pas de 30 minutes).

- Vue « classe » (programme, niveau, semestre d'une session) : la Direction Générale des Études y dépose les matières,
  déplace et étire les cours, choisit le type, la salle et les enseignants (plusieurs possibles). Les conflits sont
  signalés (rouge clair) ; on peut forcer.
- Vues « enseignant » et « salle » : consultation de la semaine d'un enseignant ou de l'occupation d'une salle.
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

MODES = [('classe', 'Classe'), ('enseignant', 'Enseignant'), ('salle', 'Salle')]
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

def _blocs(sel, analyses):
    """Par jour, les blocs à dessiner : cours qui se chevauchent regroupés (texte de chacun, conflit éventuel)."""
    par_jour = {j: [] for j, _ in Seance.JOURS}
    for s, m in sorted(analyses, key=lambda x: (x[0].jour, x[0].debut)):
        lignes = [f"{s.matiere.nom} ({Seance.COURTS.get(s.type, s.type)})"]
        if sel['mode'] != 'classe':
            lignes.append(emplois.libelle_classe(s.matiere))
        if s.salle_id and sel['mode'] != 'salle':
            lignes.append(f"Salle {s.salle.nom} ({s.salle.batiment.code or s.salle.batiment.nom})")
        if sel['mode'] != 'enseignant':
            noms = ', '.join(f"{e.prenom[:1]}. {e.nom}" for e in s.enseignants.all())
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


def _imprimer(request, sel, analyses):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle
    from .pdf_listes import imprimer_sections, section
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
        for b in _blocs(sel, analyses)[jour]:
            r0 = 1 + (b['debut'] - Seance.DEBUT_JOURNEE) // Seance.PAS
            r1 = (b['fin'] - Seance.DEBUT_JOURNEE) // Seance.PAS
            texte = '<br/><br/>'.join('<br/>'.join(f"<b>{l}</b>" if k == 0 else l for k, l in enumerate(t))
                                      for t in b['textes'])
            donnees[r0][col] = Paragraph(texte, style)
            regles += [('SPAN', (col, r0), (col, r1)),
                       ('BACKGROUND', (col, r0), (col, r1), colors.HexColor('#fecaca' if b['conflit'] else '#e0ecf7'))]
    largeur_jour = (842 - 60 - 45) / len(jours)
    grille = Table(donnees, colWidths=[45] + [largeur_jour] * len(jours), rowHeights=[14] + [13.4] * len(creneaux))
    grille.setStyle(TableStyle(regles))
    filtres = [('Emploi du temps', _titre(sel))]
    conflits = sum(1 for _, m in analyses if m)
    total = f"{len(analyses)} cours" + (f" · {conflits} en conflit (rouge clair)" if conflits else '')
    return imprimer_sections(request, 'emploi_du_temps', 'EMPLOI DU TEMPS', filtres, [section(avant=[grille])],
                             paysage=True, total=total)


def _export(sel, analyses):
    import io
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Emploi du temps'
    ws.append([_titre(sel)])
    ws['A1'].font = Font(bold=True, size=12)
    ws.append(['Heure'] + [lib for _, lib in Seance.JOURS])
    for cell in ws[2]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C')
        cell.alignment = Alignment(horizontal='center')
    for _, lib in emplois.creneaux():
        ws.append([lib])
    fin_trait = Side(style='thin', color='CBD5E1')
    for col, (jour, _) in enumerate(Seance.JOURS, start=2):
        for b in _blocs(sel, analyses)[jour]:
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
    flux = io.BytesIO()
    wb.save(flux)
    reponse = HttpResponse(flux.getvalue(), content_type=XLSX)
    reponse['Content-Disposition'] = 'attachment; filename="emploi_du_temps.xlsx"'
    return reponse
