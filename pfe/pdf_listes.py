"""
Impression (PDF) des listes : étudiants, préinscrits, congés académiques, soutenances, demandes en ligne,
et notes (liste des notes, notes d'une matière, résultats du semestre, grille de délibération).

- En-tête officiel de l'IST (pfe/entete.py) sur la première page : service et coordonnées du signataire.
- Signature en bas de la liste : TOUJOURS le chef du service (scolarité, département, Direction Générale des
  Études…), jamais la personne qui imprime (pfe/signataires.py, signataire_du_profil).
- Titre de la liste, puis SEULEMENT les filtres choisis par l'utilisateur (« Session : 2025 - 2026 · Programme : … »).
- Tableau aux colonnes libres (portrait ou paysage) ; en-têtes répétés sur chaque page ; pied de page : date
  d'impression et numéro de page.
"""
import io

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import CondPageBreak, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.pdfgen import canvas as rl_canvas
from xml.sax.saxutils import escape

MARGE = 36
BLEU = colors.HexColor('#41627C')
GRIS = colors.HexColor('#4B5563')
LIGNE_ALTERNEE = colors.HexColor('#F3F6F9')
VERT, ROUGE, GRIS_CLAIR = colors.HexColor('#DCFCE7'), colors.HexColor('#FEE2E2'), colors.HexColor('#F3F4F6')

# Liste d'étudiants : (intitulé, largeur relative)
COLONNES_ETUDIANTS = [('N°', 24), ('Matricule / INE', 92), ('Prénom et nom', 146), ('Téléphone', 72),
                      ('Niveau / Promotion', 86), ('Programme', 103)]


def _style(nom, taille=8, gras=False, couleur=None, align=0, italique=False):
    police = 'Helvetica-Bold' if gras else ('Helvetica-Oblique' if italique else 'Helvetica')
    return ParagraphStyle(nom, fontName=police, fontSize=taille, leading=taille * 1.2, alignment=align,
                          textColor=couleur or colors.black)


_cellule = _style('cellule')
_titre = _style('titre', 14, True, BLEU, 1)
_filtres = _style('filtres', 9, align=1)
_total = _style('total', 8.5, couleur=GRIS, align=1, italique=True)
_signature_date = _style('signature_date', 9.5, align=1)
_signature_fonction = _style('signature_fonction', 10, True, align=1)


def nom_complet(prenom, nom):
    return ' '.join(x for x in (str(prenom or '').strip(), str(nom or '').strip().upper()) if x)


def fmt_note(v):
    """6.5 → « 6,50 » ; vide → « — »."""
    return '—' if v is None else f"{v:.2f}".replace('.', ',')


def cellule(texte='', bas='', gras=False, align='g', fond=None, taille=8):
    """
    Cellule du tableau : `texte` (et `bas`, en petit et gris sur une seconde ligne), `align` 'g', 'c' ou 'd',
    `fond` : couleur de la case (note validée, non validée…).
    """
    return {'texte': texte, 'bas': bas, 'gras': gras, 'align': align, 'fond': fond, 'taille': taille}


def _paragraphe(c, taille_defaut):
    if not isinstance(c, dict):
        c = cellule(c)
    taille = c.get('taille') or taille_defaut
    style = _style('c', taille, c['gras'], align={'g': 0, 'c': 1, 'd': 2}[c['align']])
    texte = escape(str(c['texte'] if c['texte'] not in (None, '') else '—'))
    if c['bas']:
        texte += f'<br/><font size="{taille - 1}" color="#4B5563">{escape(str(c["bas"]))}</font>'
    return Paragraph(texte, style), c.get('fond')


class _CanvasNumerote(rl_canvas.Canvas):
    """Canvas qui connaît le nombre total de pages (« page 2 / 5 ») et imprime le pied de page."""
    def __init__(self, *args, imprime_le='', **kwargs):
        super().__init__(*args, **kwargs)
        self._pages, self._imprime_le = [], imprime_le

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        largeur = self._pagesize[0]
        for etat in self._pages:
            self.__dict__.update(etat)
            self.setFont('Helvetica', 7.5)
            self.setFillColor(GRIS)
            self.drawString(MARGE, 22, self._imprime_le)
            self.drawRightString(largeur - MARGE, 22, f'Page {self._pageNumber} / {total}')
            super().showPage()
        super().save()


def _bloc_signature(signataire, largeur_utile):
    """À droite, après le tableau : lieu et date, fonction du chef, espace pour signer, son nom."""
    lignes = [[Paragraph(f"Mamou, le {timezone.localdate():%d/%m/%Y}", _signature_date)],
              [Paragraph(escape(signataire.fonction), _signature_fonction)], [Spacer(1, 52)],
              [Paragraph(escape(signataire.nom) if signataire.nom else '&nbsp;', _signature_fonction)]]
    bloc = Table(lignes, colWidths=[230])
    bloc.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    cadre = Table([['', bloc]], colWidths=[largeur_utile - 230, 230])
    cadre.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    return KeepTogether([Spacer(1, 18), cadre])


_sous_titre = _style('sous_titre', 10.5, True, BLEU)
_note = _style('note', 7.5, couleur=GRIS, italique=True)


def _tableau(colonnes, lignes, utile, taille=8, vide='Aucune ligne avec ces filtres.', pied=None):
    """Tableau : en-tête bleu répété sur chaque page, lignes alternées, `pied` : ligne de total en gras."""
    poids = sum(w for _, w in colonnes)
    largeurs = [utile * w / poids for _, w in colonnes]
    entete_style = _style('entete', taille, True, colors.white)
    donnees = [[Paragraph(escape(t), entete_style) for t, _ in colonnes]]
    style = [('BACKGROUND', (0, 0), (-1, 0), BLEU), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
             ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#C9D1DA')),
             ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
             ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3)]
    for i, ligne in enumerate(lignes, start=1):
        rang = []
        if i % 2 == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), LIGNE_ALTERNEE))
        for j, c in enumerate(ligne):
            p, fond = _paragraphe(c, taille)
            rang.append(p)
            if fond is not None:
                style.append(('BACKGROUND', (j, i), (j, i), fond))
        donnees.append(rang)
    if not lignes:
        donnees.append([Paragraph(escape(vide), _cellule)] + [''] * (len(colonnes) - 1))
        style.append(('SPAN', (0, 1), (-1, 1)))
    elif pied:
        i = len(donnees)
        donnees.append([_paragraphe({**(c if isinstance(c, dict) else cellule(c)), 'gras': True}, taille)[0]
                        for c in pied])
        style += [('BACKGROUND', (0, i), (-1, i), colors.HexColor('#E5EAF0')),
                  ('LINEABOVE', (0, i), (-1, i), 1, BLEU)]
    tableau = Table(donnees, colWidths=largeurs, repeatRows=1)
    tableau.setStyle(TableStyle(style))
    return tableau


def section(titre='', colonnes=(), lignes=(), pied=None, note='', avant=(), vide='Aucune donnée pour ces filtres.'):
    """Une partie du document : sous-titre, note, éléments libres (graphique…), tableau avec ligne de total."""
    return {'titre': titre, 'colonnes': list(colonnes), 'lignes': list(lignes), 'pied': pied, 'note': note,
            'avant': list(avant), 'vide': vide}


def pdf_sections(titre, filtres, sections, utilisateur=None, signataire=None, paysage=False, total='', taille=8):
    """
    Document à plusieurs tableaux (statistiques…) : en-tête de l'IST, `titre`, `filtres` choisis, `total` (ligne de
    chiffres clés), puis chaque section(), et la signature du chef du service. Renvoie le PDF (octets).
    """
    from .entete import RAPPORT_BANDEAU, dessiner_entete
    from .signataires import signataire as signataire_par_defaut
    signataire = signataire or signataire_par_defaut()
    page = landscape(A4) if paysage else A4
    largeur, hauteur = page
    g, d = MARGE, largeur - MARGE
    utile = d - g
    flux = io.BytesIO()

    def premiere_page(c, doc):
        c.saveState()
        # Bandeau de la même taille qu'en portrait, centré
        dessiner_entete(c, largeur, hauteur, g, d, haut=18, largeur_bandeau=A4[0] - 2 * MARGE, taille_service=11,
                        taille_coordonnees=8, signataire=signataire)
        c.restoreState()

    haut_premiere = 18 + (A4[0] - 2 * MARGE) * RAPPORT_BANDEAU + 30
    doc = SimpleDocTemplate(flux, pagesize=page, leftMargin=g, rightMargin=MARGE, topMargin=MARGE, bottomMargin=40,
                            title=titre, author='gePed · IST de Mamou')
    filtres = [(lib, val) for lib, val in filtres if val not in (None, '')]
    contenu = [Spacer(1, haut_premiere - MARGE + 14), Paragraph(escape(titre), _titre), Spacer(1, 4)]
    if filtres:
        contenu.append(Paragraph(' &nbsp;·&nbsp; '.join(f'<b>{escape(lib)} :</b> {escape(str(val))}'
                                                         for lib, val in filtres), _filtres))
    if total:
        contenu.append(Paragraph(escape(total), _total))
    contenu.append(Spacer(1, 8))
    # Les tableaux s'enchaînent sans saut de page forcé : le premier commence TOUJOURS sur la première page, sous
    # l'en-tête, et continue sur les pages suivantes (en-têtes de colonnes répétés). Seule précaution : le titre
    # d'une section suivante n'est jamais laissé seul en bas d'une page (CondPageBreak).
    for k, sec in enumerate(sections):
        if k and (sec['titre'] or sec['note']):
            contenu.append(CondPageBreak(90))
        if sec['titre']:
            contenu += [Spacer(1, 6 if k else 0), Paragraph(escape(sec['titre']), _sous_titre), Spacer(1, 3)]
        if sec['note']:
            contenu += [Paragraph(escape(sec['note']), _note), Spacer(1, 3)]
        contenu += sec['avant']
        if sec['colonnes']:
            contenu.append(_tableau(sec['colonnes'], sec['lignes'], utile, taille, sec['vide'], sec['pied']))
        contenu.append(Spacer(1, 8))
    contenu.append(_bloc_signature(signataire, utile))

    qui = f" par {utilisateur.get_full_name() or utilisateur.get_username()}" if utilisateur is not None else ''
    imprime_le = f"gePed · imprimé le {timezone.localtime():%d/%m/%Y à %H:%M}{qui}"
    doc.build(contenu, onFirstPage=premiere_page,
              canvasmaker=lambda *a, **k: _CanvasNumerote(*a, imprime_le=imprime_le, **k))
    return flux.getvalue()


def pdf_tableau(titre, filtres, colonnes, lignes, utilisateur=None, signataire=None, paysage=False, total='',
                vide='Aucune ligne avec ces filtres.', avant=(), taille=8):
    """
    Document à un seul tableau : `colonnes` : [(intitulé, largeur relative)] ; `lignes` : listes de cellules (texte
    ou cellule()) ; `avant` : éléments à placer avant le tableau. Voir pdf_sections().
    """
    return pdf_sections(titre, filtres, [section('', colonnes, lignes, avant=avant, vide=vide)], utilisateur,
                        signataire, paysage, total, taille)


def pdf_liste_etudiants(titre, filtres, lignes, utilisateur=None, signataire=None):
    """
    Liste d'étudiants : `lignes` : [{'matricule', 'ine', 'prenom', 'nom', 'telephone', 'niveau', 'promotion',
    'programme'}] ; 'session' (facultatif) : l'année, sous la promotion, quand la liste mélange plusieurs sessions.
    """
    rangs = [[cellule(k, align='c'), cellule(l.get('matricule'), f"INE {l['ine']}" if l.get('ine') else ''),
              nom_complet(l.get('prenom'), l.get('nom')), l.get('telephone') or '',
              cellule(l.get('niveau'), ' · '.join(x for x in (l.get('promotion'), l.get('session')) if x)),
              l.get('programme') or ''] for k, l in enumerate(lignes, start=1)]
    n = len(lignes)
    return pdf_tableau(titre, filtres, COLONNES_ETUDIANTS, rangs, utilisateur, signataire,
                       total=f"{n} étudiant{'s' if n > 1 else ''}", vide='Aucun étudiant avec ces filtres.')


def _signataire(request):
    from .roles import get_profil
    from .signataires import signataire_du_profil
    return signataire_du_profil(get_profil(request))


def imprimer_liste(request, titre, filtres, lignes, nom_fichier):
    """Réponse PDF d'une liste d'étudiants, signée par le chef du service de la personne qui imprime."""
    return reponse_pdf(pdf_liste_etudiants(titre, filtres, lignes, request.user, _signataire(request)), nom_fichier)


def imprimer_sections(request, nom_fichier, titre, filtres, sections, **options):
    """Réponse PDF d'un document à plusieurs tableaux (statistiques), signé par le chef du service."""
    return reponse_pdf(pdf_sections(titre, filtres, sections, request.user, _signataire(request), **options),
                       nom_fichier)


def imprimer_tableau(request, nom_fichier, titre, filtres, colonnes, lignes, **options):
    """Réponse PDF d'un tableau (notes, résultats, grille du jury), signé par le chef du service."""
    return reponse_pdf(pdf_tableau(titre, filtres, colonnes, lignes, request.user, _signataire(request), **options),
                       nom_fichier)


def reponse_pdf(contenu, nom):
    """Réponse HTTP qui ouvre le PDF dans le navigateur (impression)."""
    from django.http import HttpResponse
    reponse = HttpResponse(contenu, content_type='application/pdf')
    reponse['Content-Disposition'] = f'inline; filename="{nom}.pdf"'
    return reponse
