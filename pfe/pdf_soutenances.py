"""
PDF des soutenances (ReportLab) : PV de soutenance d'un projet (A4 portrait) et calendrier
à afficher (A4 paysage). Signatures : noms en texte avec un espace vide pour signer à la main,
sans cachet (comme l'attestation de fin d'études).
"""
import io
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .attestations import date_longue, libelle_cycle

BLEU = colors.HexColor('#41627c')
BLEU_PALE = colors.HexColor('#eef3f7')
GRIS = colors.HexColor('#c9d3dc')

NORMAL = ParagraphStyle('normal', fontName='Helvetica', fontSize=9.5, leading=12.5)
PETIT = ParagraphStyle('petit', parent=NORMAL, fontSize=8.5, leading=11)
GRAS = ParagraphStyle('gras', parent=NORMAL, fontName='Helvetica-Bold')
ENTETE = ParagraphStyle('entete', parent=NORMAL, alignment=TA_CENTER, fontSize=9, leading=11.5)
TITRE = ParagraphStyle('titre', parent=NORMAL, alignment=TA_CENTER, fontName='Helvetica-Bold', fontSize=15,
                       leading=19, textColor=BLEU, spaceBefore=6, spaceAfter=10)
SECTION = ParagraphStyle('section', parent=GRAS, fontSize=10.5, textColor=BLEU, spaceBefore=10, spaceAfter=4)
CENTRE = ParagraphStyle('centre', parent=NORMAL, alignment=TA_CENTER)
CENTRE_GRAS = ParagraphStyle('centre_gras', parent=GRAS, alignment=TA_CENTER)
BLANC = ParagraphStyle('blanc', parent=GRAS, textColor=colors.white, fontSize=9)


def _p(texte, style=NORMAL):
    return Paragraph(escape(str(texte or '')).replace('\n', '<br/>'), style)


def _fmt(note):
    return '—' if note is None else ('%g' % note).replace('.', ',')


def _logo(taille):
    chemin = settings.GPED_ATTESTATION['dossier_images'] / 'logo.png'
    return Image(str(chemin), width=taille, height=taille * 60 / 64) if chemin.exists() else ''


def _entete(largeur, sous_titre=''):
    lignes = ["<b>RÉPUBLIQUE DE GUINÉE</b>", "Travail – Justice – Solidarité",
              "MINISTÈRE DE L'ENSEIGNEMENT SUPÉRIEUR ET DE LA RECHERCHE SCIENTIFIQUE",
              "<font color='#000099' size='12'><b>INSTITUT SUPÉRIEUR DE TECHNOLOGIE DE MAMOU</b></font>"]
    if sous_titre:
        lignes.append(f"<b>{escape(sous_titre)}</b>")
    t = Table([[_logo(1.7 * cm), Paragraph('<br/>'.join(lignes), ENTETE), '']],
              colWidths=[2.2 * cm, largeur - 4.4 * cm, 2.2 * cm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, 0), 0.8, BLEU),
                           ('BOTTOMPADDING', (0, 0), (-1, 0), 6)]))
    return t


def _tableau(donnees, largeurs, entete=True, zebre=True):
    t = Table(donnees, colWidths=largeurs, repeatRows=1 if entete else 0)
    style = [('GRID', (0, 0), (-1, -1), 0.5, GRIS), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
             ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]
    if entete:
        style.append(('BACKGROUND', (0, 0), (-1, 0), BLEU))
    if zebre:
        for i in range(1 if entete else 0, len(donnees)):
            if i % 2 == 0:
                style.append(('BACKGROUND', (0, i), (-1, i), BLEU_PALE))
    t.setStyle(TableStyle(style))
    return t


def _bas_de_page(c, doc):
    c.saveState()
    c.setFont('Helvetica', 7.5)
    c.setFillColor(colors.grey)
    c.drawString(doc.leftMargin, 0.9 * cm, f"gePed · IST de Mamou · imprimé le {timezone.localdate():%d/%m/%Y}")
    c.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.9 * cm, f"Page {doc.page}")
    c.restoreState()


# ---------------------------------------------------------------- PV de soutenance

def pdf_pv(projet, membres):
    from .notes import est_validee
    flux = io.BytesIO()
    doc = SimpleDocTemplate(flux, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.3 * cm,
                            bottomMargin=1.6 * cm, title=f"PV de soutenance – {projet.intitule}",
                            author='gePed – IST de Mamou')
    largeur = A4[0] - doc.leftMargin - doc.rightMargin
    p = projet
    cycle = libelle_cycle(p.cycle, 'texte') or p.cycle or '—'
    cycle = cycle[:1].upper() + cycle[1:]
    histoire = [_entete(largeur, f"Programme : {p.filiere}"), Spacer(1, 8),
                Paragraph('PROCÈS-VERBAL DE SOUTENANCE', TITRE)]

    creneau = ' · '.join(x for x in (date_longue(p.date) if p.date else '', p.heure.strftime('%Hh%M') if p.heure else '',
                                     f"salle {p.salle}" if p.salle else '') if x) or '—'
    infos = [('Projet', p.intitule), ('Thème', p.theme or '—'), ('Cycle', cycle), ('Session', p.session.annee),
             ('Promotion', str(p.promotion)), ('Date, heure et salle', creneau), ('Encadreur', p.encadreur or '—')]
    if p.maitre_stage or p.entreprise:
        infos.append(('Maître de stage', ' – '.join(x for x in (p.maitre_stage, p.entreprise) if x)))
    t = Table([[_p(k, GRAS), _p(v)] for k, v in infos], colWidths=[4.2 * cm, largeur - 4.2 * cm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), 0.3, GRIS),
                           ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    histoire += [t, Paragraph('Jury', SECTION)]
    jury = [[_p('Qualité', BLANC), _p('Nom', BLANC)]] + [[_p(q), _p(n or '……………………………………………………')] for q, n in (
        ('Président du jury', p.president_jury), ('Examinateur', p.examinateur), ('Rapporteur', p.rapporteur))]
    histoire.append(_tableau(jury, [5 * cm, largeur - 5 * cm]))

    histoire.append(Paragraph(f"Résultats ({len(membres)} étudiant{'s' if len(membres) > 1 else ''})", SECTION))
    lignes = [[_p(t, BLANC) for t in ('N°', 'Matricule', 'Nom et prénom(s)', 'Note / 10', 'Mention', 'Décision')]]
    for i, s in enumerate(membres, start=1):
        e = s.etudiant
        note = _fmt(s.note_finale) + (' (Session)' if s.par_session else '')
        decision = '—' if s.note_finale is None else ('Validée' if est_validee(s.note_finale) else 'Non validée')
        lignes.append([_p(i, CENTRE), _p(e.matricule or '—', CENTRE), _p(f"{e.nom.upper()} {e.prenom}", GRAS),
                       _p(note, CENTRE_GRAS), _p(s.mention or '—', CENTRE), _p(decision, CENTRE)])
    if not membres:
        lignes.append([_p('Aucun étudiant dans ce projet.'), '', '', '', '', ''])
    histoire.append(_tableau(lignes, [1 * cm, 2.6 * cm, largeur - 11.6 * cm, 2.4 * cm, 2.6 * cm, 3 * cm]))

    fait = p.date or timezone.localdate()
    histoire += [Spacer(1, 14), Paragraph(f"Fait à {settings.GPED_ATTESTATION['ville']}, le {date_longue(fait)}",
                                          ParagraphStyle('fait', parent=NORMAL, alignment=2))]
    signataires = [('Le Président du jury', p.president_jury), ("L'Encadreur", p.encadreur),
                   ('Le Chef de département', p.chef_departement)]
    col = largeur / 3
    signatures = Table([[_p(t, CENTRE_GRAS) for t, _ in signataires],
                        ['', '', ''],
                        [_p(n or '', CENTRE) for _, n in signataires]],
                       colWidths=[col] * 3, rowHeights=[None, 2.4 * cm, None])
    signatures.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    histoire += [Spacer(1, 10), KeepTogether(signatures)]
    doc.build(histoire, onFirstPage=_bas_de_page, onLaterPages=_bas_de_page)
    return flux.getvalue()


# ---------------------------------------------------------------- calendrier

def pdf_calendrier(projets, titre=''):
    flux = io.BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(flux, pagesize=page, leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.1 * cm,
                            bottomMargin=1.5 * cm, title='Calendrier des soutenances', author='gePed – IST de Mamou')
    largeur = page[0] - doc.leftMargin - doc.rightMargin
    histoire = [_entete(largeur), Spacer(1, 6), Paragraph('CALENDRIER DES SOUTENANCES', TITRE)]
    if titre:
        histoire.append(Paragraph(escape(titre), ParagraphStyle('st', parent=CENTRE_GRAS, spaceAfter=8)))
    lignes = [[_p(t, BLANC) for t in ('Date', 'Heure', 'Salle', 'Projet et thème', 'Étudiants', 'Encadreur', 'Jury')]]
    for p in projets:
        etudiants = '<br/>'.join(escape(f"{s.etudiant.nom.upper()} {s.etudiant.prenom}") for s in p.membres.all())
        jury = '<br/>'.join(escape(f"{q} : {n}") for q, n in p.jury)
        lignes.append([_p(p.date.strftime('%d/%m/%Y') if p.date else '—', PETIT),
                       _p(p.heure.strftime('%Hh%M') if p.heure else '—', PETIT), _p(p.salle or '—', PETIT),
                       Paragraph(f"<b>{escape(p.intitule)}</b>" + (f"<br/>{escape(p.theme)}" if p.theme else ''), PETIT),
                       Paragraph(etudiants or '—', PETIT), _p(p.encadreur or '—', PETIT),
                       Paragraph(jury or '—', PETIT)])
    if not projets:
        lignes.append([_p('Aucune soutenance programmée avec ces filtres.'), '', '', '', '', '', ''])
    histoire.append(_tableau(lignes, [2.2 * cm, 1.5 * cm, 2.2 * cm, largeur - 20.4 * cm, 5.5 * cm, 4 * cm, 5 * cm]))
    doc.build(histoire, onFirstPage=_bas_de_page, onLaterPages=_bas_de_page)
    return flux.getvalue()
