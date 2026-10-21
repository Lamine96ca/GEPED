"""
Attestation de soutenance (A4 paysage, ReportLab) — design propre à ce document :

  - bandeau vertical bleu nuit à gauche : logo dans un médaillon, établissement, n° de
    référence et QR code de vérification ;
  - grand titre à empattements, filets dorés, rosace de sécurité très pâle en filigrane ;
  - le thème du mémoire mis en valeur dans un encadré ; date, note et mention en chiffres clés ;
  - quatre signataires : Président du jury, Encadreur, Chef de département (saisis sur le projet
    de soutenance) et Chef du Service de la Scolarité. Noms imprimés, espace vide pour signer :
    pas de cachet ni de signature numérisés.

Même numérotation continue et même vérification publique (QR) que les autres attestations.
"""
import io
import math
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Frame, Paragraph

from .attestations import HAUTEUR, LARGEUR, _image, _nom_programme, _polices_fin_etudes, date_longue, libelle_cycle

NUIT = (0.055, 0.165, 0.278)          # #0e2a47
OR = (0.769, 0.616, 0.235)            # #c49d3c
OR_PALE = (0.984, 0.965, 0.914)       # #fbf6e9
ENCRE = (0.13, 0.15, 0.19)
GRIS = (0.42, 0.45, 0.5)
BANDE = 196                            # largeur du bandeau gauche
X0, X1 = BANDE + 46, LARGEUR - 44      # zone de contenu
TITRE_DOC = 'ATTESTATION DE SOUTENANCE'


def soutenance_de(attestation):
    from .models import Soutenance
    return (Soutenance.objects.filter(etudiant=attestation.etudiant, cycle=attestation.cycle)
            .select_related('projet', 'promotion', 'session', 'filiere').first())


def _conf(cle):
    return settings.GPED_ATTESTATION[cle]


def _ecrit_espace(c, x, y, texte, police, taille, espace, centre=False, droite=False):
    """Texte espacé (lettres écartées), éventuellement centré sur x ou aligné à droite."""
    largeur = stringWidth(texte, police, taille) + espace * (len(texte) - 1)
    if centre:
        x -= largeur / 2
    elif droite:
        x -= largeur
    t = c.beginText(x, y)
    t.setFont(police, taille)
    t.setCharSpace(espace)
    t.textOut(texte)
    t.setCharSpace(0)                  # l'espacement resterait sinon actif pour tous les textes suivants
    c.drawText(t)
    return largeur


def _rosace(c, cx, cy, rayon, couleur=(0.93, 0.89, 0.8)):
    """Motif guilloché (rosace de sécurité) : ellipses pivotées, traits très fins et pâles."""
    c.saveState()
    c.setStrokeColorRGB(*couleur)
    c.setLineWidth(0.35)
    for i in range(36):
        c.saveState()
        c.translate(cx, cy)
        c.rotate(i * 5)
        c.ellipse(-rayon, -rayon * 0.42, rayon, rayon * 0.42, stroke=1, fill=0)
        c.restoreState()
    for k in range(1, 4):
        c.circle(cx, cy, rayon * (0.18 + 0.27 * k), stroke=1, fill=0)
    c.restoreState()


def _bandeau(c, attestation, url_verification, normale, grasse):
    c.setFillColorRGB(*NUIT)
    c.rect(0, 0, BANDE, HAUTEUR, stroke=0, fill=1)
    c.setFillColorRGB(*OR)
    c.rect(BANDE, 0, 3, HAUTEUR, stroke=0, fill=1)
    # Médaillon du logo
    cx, cy = BANDE / 2, HAUTEUR - 92
    c.setFillColorRGB(1, 1, 1)
    c.circle(cx, cy, 46, stroke=0, fill=1)
    c.setStrokeColorRGB(*OR)
    c.setLineWidth(1.4)
    c.circle(cx, cy, 51, stroke=1, fill=0)
    c.drawImage(_image('logo'), cx - 34, cy - 32.5, 68, 65, mask='auto', preserveAspectRatio=True)
    # Établissement
    c.setFillColorRGB(1, 1, 1)
    y = cy - 78
    for ligne in ('INSTITUT SUPÉRIEUR', 'DE TECHNOLOGIE', 'DE MAMOU'):
        _ecrit_espace(c, cx, y, ligne, grasse, 10.5, 1.2, centre=True)
        y -= 14
    c.setStrokeColorRGB(*OR)
    c.setLineWidth(0.8)
    c.line(cx - 28, y + 2, cx + 28, y + 2)
    c.setFillColorRGB(0.8, 0.85, 0.9)
    _ecrit_espace(c, cx, y - 14, 'SERVICE DE LA SCOLARITÉ', normale, 7.2, 0.9, centre=True)
    c.setFont(normale, 7)
    for i, ligne in enumerate((_conf('boite_postale'), f"Tél : {_conf('telephone')}", _conf('email'))):
        c.drawCentredString(cx, y - 32 - i * 10, ligne)
    # Référence
    y_ref = 228
    c.setFillColorRGB(*OR)
    _ecrit_espace(c, cx, y_ref + 16, 'RÉFÉRENCE', grasse, 7, 1.6, centre=True)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(grasse, 11.5)
    c.drawCentredString(cx, y_ref, attestation.num_ref)
    # QR code de vérification, sur un fond blanc arrondi
    if url_verification:
        from reportlab.graphics import renderPDF
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        taille, bas = 86, 69
        c.setFillColorRGB(1, 1, 1)
        c.roundRect(cx - taille / 2 - 7, bas - 7, taille + 14, taille + 14, 6, stroke=0, fill=1)
        qr = QrCodeWidget(url_verification, barLevel='M', barBorder=0)
        x0, y0, x1, y1 = qr.getBounds()
        dessin = Drawing(taille, taille, transform=[taille / (x1 - x0), 0, 0, taille / (y1 - y0), 0, 0])
        dessin.add(qr)
        renderPDF.draw(dessin, c, cx - taille / 2, bas)
        c.setFillColorRGB(0.8, 0.85, 0.9)
        c.setFont(normale, 6.8)
        c.drawCentredString(cx, 45, "Scannez pour vérifier")
        c.drawCentredString(cx, 36, "l'authenticité du document")


def _paragraphe(e, s, attestation):
    madame = e.sexe == 'F'
    civilite = 'Mme' if madame else 'M.'
    nom = f"{escape(e.prenom.upper())} {escape(e.nom.upper())}"
    ine = f", INE <b>{escape(e.ine)}</b>" if e.ine else ''
    programme = escape(_nom_programme(attestation.filiere)) if attestation.filiere else ''
    cycle = libelle_cycle(attestation.cycle, 'texte') or attestation.cycle or ''
    promotion = f", promotion <b>{escape(str(s.promotion))}</b>" if s and s.promotion_id else ''
    date = f" le <b>{date_longue(s.projet.date)}</b>," if s and s.projet and s.projet.date else ''
    return (f"Les soussignés attestent que {civilite} <b>{nom}</b>, matricule <b>{escape(e.matricule or '—')}</b>{ine}, "
            f"{'étudiante' if madame else 'étudiant'} du département <b>{programme}</b>{promotion}, "
            f"a présenté et soutenu avec succès{date} devant le jury, son mémoire de fin de "
            f"{escape(cycle) if cycle else 'cycle'} à l'Institut Supérieur de Technologie de Mamou, au titre de "
            f"l'année universitaire <b>{escape(str(attestation.session or ''))}</b>.")


def contenu_soutenance(attestation):
    """Titre et texte (utilisés aussi par la page publique de vérification)."""
    return TITRE_DOC, [_paragraphe(attestation.etudiant, soutenance_de(attestation), attestation)]


def pdf_soutenance(attestation, url_verification=None):
    from .notes import mention
    normale, grasse = _polices_fin_etudes()
    e, s = attestation.etudiant, soutenance_de(attestation)
    p = s.projet if s else None
    flux = io.BytesIO()
    c = Canvas(flux, pagesize=(LARGEUR, HAUTEUR))
    c.setTitle(f"Attestation de soutenance – {attestation.num_ref}")
    c.setAuthor('gePed – IST de Mamou')

    _rosace(c, LARGEUR - 18, HAUTEUR - 12, 128)
    _rosace(c, LARGEUR - 10, 10, 70, couleur=(0.95, 0.93, 0.88))
    _bandeau(c, attestation, url_verification, normale, grasse)
    centre = (X0 + X1) / 2

    # En-tête officiel
    c.setFillColorRGB(*GRIS)
    _ecrit_espace(c, centre, HAUTEUR - 46, 'RÉPUBLIQUE DE GUINÉE', grasse, 8.5, 2.2, centre=True)
    c.setFont(normale, 7.5)
    c.drawCentredString(centre, HAUTEUR - 58, 'Travail – Justice – Solidarité')
    c.drawCentredString(centre, HAUTEUR - 69, "Ministère de l'Enseignement Supérieur et de la Recherche Scientifique")

    # Titre
    c.setFillColorRGB(*NUIT)
    _ecrit_espace(c, centre, HAUTEUR - 122, 'ATTESTATION', 'Times-Bold', 44, 5, centre=True)
    c.setFillColorRGB(*OR)
    largeur = _ecrit_espace(c, centre, HAUTEUR - 146, 'DE SOUTENANCE', grasse, 13, 6.5, centre=True)
    c.setStrokeColorRGB(*OR)
    c.setLineWidth(0.9)
    for sens in (-1, 1):
        debut = centre + sens * (largeur / 2 + 14)
        c.line(debut, HAUTEUR - 141.5, debut + sens * 70, HAUTEUR - 141.5)
        c.circle(debut + sens * 74, HAUTEUR - 141.5, 1.6, stroke=0, fill=1)
    cycle_entete = libelle_cycle(attestation.cycle, 'entete') or attestation.cycle
    if cycle_entete:
        c.setFillColorRGB(*GRIS)
        _ecrit_espace(c, centre, HAUTEUR - 163, cycle_entete.upper(), normale, 7.5, 1.8, centre=True)

    # Texte
    style = ParagraphStyle('sout', fontName='Times-Roman', fontSize=13, leading=19.5, alignment=TA_JUSTIFY,
                           textColor=ENCRE)
    para = Paragraph(_paragraphe(e, s, attestation), style)
    largeur_texte = X1 - X0
    taille = 13
    while para.wrap(largeur_texte, 200)[1] > 82 and taille > 10:
        taille -= 0.5
        style = ParagraphStyle(f'sout{taille}', parent=style, fontSize=taille, leading=taille * 1.5)
        para = Paragraph(_paragraphe(e, s, attestation), style)
    Frame(X0, HAUTEUR - 268, largeur_texte, 90, leftPadding=0, rightPadding=0, topPadding=0,
          bottomPadding=0).addFromList([para], c)

    # Thème du mémoire
    haut_theme = HAUTEUR - 280
    theme = (p.theme if p and p.theme else '').strip()
    style_theme = ParagraphStyle('theme', fontName='Times-Italic', fontSize=14, leading=18, textColor=NUIT)
    texte_theme = f"« {escape(theme)} »" if theme else "« ……………………………………………………………… »"
    bloc = Paragraph(texte_theme, style_theme)
    h_theme = bloc.wrap(largeur_texte - 40, 80)[1]
    while h_theme > 56 and style_theme.fontSize > 10:
        style_theme = ParagraphStyle('theme2', parent=style_theme, fontSize=style_theme.fontSize - 0.5,
                                     leading=(style_theme.fontSize - 0.5) * 1.28)
        bloc = Paragraph(texte_theme, style_theme)
        h_theme = bloc.wrap(largeur_texte - 40, 80)[1]
    hauteur_cadre = h_theme + 30
    c.setFillColorRGB(*OR_PALE)
    c.rect(X0, haut_theme - hauteur_cadre, largeur_texte, hauteur_cadre, stroke=0, fill=1)
    c.setFillColorRGB(*OR)
    c.rect(X0, haut_theme - hauteur_cadre, 3.5, hauteur_cadre, stroke=0, fill=1)
    _ecrit_espace(c, X0 + 20, haut_theme - 13, 'THÈME DU MÉMOIRE', grasse, 7, 1.8)
    bloc.drawOn(c, X0 + 20, haut_theme - hauteur_cadre + 9)

    # Chiffres clés : date, note, mention, promotion
    y_cles = haut_theme - hauteur_cadre - 40
    cles = [('DATE DE SOUTENANCE', date_longue(p.date) if p and p.date else '—'),
            ('NOTE OBTENUE', f"{s.note_finale:.2f}".replace('.', ',') + ' / 10' if s and s.note_finale is not None else '—'),
            ('MENTION', (mention(s.note_finale) or '—') + (' (Session)' if s and s.par_session else '') if s else '—'),
            ('ANNÉE UNIVERSITAIRE', str(attestation.session or '—'))]
    largeur_cle = largeur_texte / len(cles)
    for i, (libelle, valeur) in enumerate(cles):
        x = X0 + i * largeur_cle
        if i:
            c.setStrokeColorRGB(0.85, 0.82, 0.74)
            c.setLineWidth(0.6)
            c.line(x, y_cles - 6, x, y_cles + 22)
        c.setFillColorRGB(*GRIS)
        _ecrit_espace(c, x + largeur_cle / 2, y_cles + 13, libelle, grasse, 6.5, 1.4, centre=True)
        c.setFillColorRGB(*NUIT)
        police, taille = 'Times-Bold', 14
        while stringWidth(valeur, police, taille) > largeur_cle - 12 and taille > 9:
            taille -= 0.5
        c.setFont(police, taille)
        c.drawCentredString(x + largeur_cle / 2, y_cles - 4, valeur)

    # Fait à…
    c.setFillColorRGB(*ENCRE)
    c.setFont('Times-Italic', 11)
    c.drawRightString(X1, 152, f"Fait à {_conf('ville')}, le {date_longue(attestation.date_delivrance)}")

    # Signataires
    signataires = [('Le Président du jury', p.president_jury if p else ''), ("L'Encadreur", p.encadreur if p else ''),
                   ('Le Chef de département', p.chef_departement if p else ''),
                   (_conf('fonction'), _conf('signataire'))]
    largeur_sig = largeur_texte / len(signataires)
    taille = 8.2                        # même taille pour les quatre titres : celle qui fait tenir le plus long
    while max(stringWidth(t.upper(), grasse, taille) + 0.8 * len(t) for t, _ in signataires) > largeur_sig - 8 \
            and taille > 6:
        taille -= 0.2
    for i, (titre, nom) in enumerate(signataires):
        x = X0 + i * largeur_sig + largeur_sig / 2
        c.setFillColorRGB(*NUIT)
        _ecrit_espace(c, x, 128, titre.upper(), grasse, taille, 0.8, centre=True)
        c.setStrokeColorRGB(*OR)
        c.setLineWidth(0.7)
        c.line(x - largeur_sig / 2 + 16, 58, x + largeur_sig / 2 - 16, 58)
        c.setFillColorRGB(*ENCRE)
        c.setFont(normale, 9)
        texte = nom or '…………………………'
        while stringWidth(texte, normale, 9) > largeur_sig - 12 and len(texte) > 4:
            texte = texte[:-2] + '…'
        c.drawCentredString(x, 46, texte)

    # Mentions de bas de page
    c.setFillColorRGB(*GRIS)
    c.setFont('Times-Italic', 7.5)
    c.drawCentredString(centre, 22, "La présente attestation est délivrée pour servir et valoir ce que de droit. "
                                    "Toute rature ou surcharge la rend nulle.")
    c.showPage()
    c.save()
    return flux.getvalue()
