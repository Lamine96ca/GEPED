"""
Attestations gePed (PDF, A4 paysage) avec ReportLab.

Toutes les attestations partagent le même CADRE (bordures, filigrane, logo, en-tête officiel,
coordonnées, n° de référence, date, mention de bas de page, signature). Chaque type ne
fournit que son titre et ses paragraphes : voir `contenu_inscription`.

Les positions reprennent le modèle « ATTESTATION INSCRIPTION.pdf » de l'IST de Mamou
(coordonnées en points, mesurées depuis le HAUT de la page puis converties).
"""
import io
import unicodedata
from functools import lru_cache

from django.conf import settings
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Frame, Paragraph, Spacer

from .models import Attestation, Semestre

LARGEUR, HAUTEUR = landscape(A4)            # 841.9 x 595.3 pt
MARGE_G, MARGE_D = 53.2, 790.0              # bords du texte (en-tête, réf., date)
MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
        'septembre', 'octobre', 'novembre', 'décembre']


def _conf(cle):
    return settings.GPED_ATTESTATION[cle]


def _image(nom):
    return ImageReader(str(_conf('dossier_images') / f'{nom}.png'))


def _y(haut, taille):
    """Ligne de base ReportLab pour un texte dont le HAUT est à `haut` pt du haut de la page."""
    return HAUTEUR - haut - taille * 0.82


def _cle(texte):
    s = unicodedata.normalize('NFKD', str(texte or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def date_longue(d):
    """28 septembre 2026 ; 1er octobre 2026."""
    jour = '1er' if d.day == 1 else str(d.day)
    return f"{jour} {MOIS[d.month - 1]} {d.year}"


def _centre(c, texte, haut, police, taille, largeur_max=700):
    """Texte centré, réduit si besoin pour tenir dans `largeur_max`."""
    while stringWidth(texte, police, taille) > largeur_max and taille > 8:
        taille -= 0.5
    c.setFont(police, taille)
    c.drawCentredString(LARGEUR / 2, _y(haut, taille), texte)


# ---------------------------------------------------------------- cadre commun

def dessiner_qr(c, url, x, haut, taille):
    """QR code (ReportLab, sans dépendance) + mention, `haut` mesuré depuis le haut de la page."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    qr = QrCodeWidget(url, barLevel='M', barBorder=1)
    x0, y0, x1, y1 = qr.getBounds()
    dessin = Drawing(taille, taille, transform=[taille / (x1 - x0), 0, 0, taille / (y1 - y0), 0, 0])
    dessin.add(qr)
    c.setFillColorRGB(1, 1, 1)
    c.rect(x - 2, HAUTEUR - haut - taille - 2, taille + 4, taille + 4, stroke=0, fill=1)   # fond blanc sur le filigrane
    renderPDF.draw(dessin, c, x, HAUTEUR - haut - taille)
    c.setFillColorRGB(0, 0, 0)
    c.setFont('Helvetica', 7)
    c.drawCentredString(x + taille / 2, HAUTEUR - haut - taille - 10, "Scannez pour vérifier")
    c.drawCentredString(x + taille / 2, HAUTEUR - haut - taille - 18, "l'authenticité du document")


@lru_cache(maxsize=1)
def _logo_filigrane():
    """Logo pâle du filigrane, recadré (l'image d'origine est une page entière, logo au milieu)."""
    from PIL import Image
    with Image.open(_conf('dossier_images') / 'filigrane.png') as img:
        img = img.convert('RGB')
    zone = Image.eval(img.convert('L'), lambda v: 255 if v < 250 else 0).getbbox() or (0, 0, *img.size)
    flux = io.BytesIO()
    img.crop(zone).save(flux, 'PNG', optimize=True)
    return flux.getvalue()


def dessiner_filigrane(c, haut, bas=548):
    """Filigrane réduit pour tenir entre `haut` et `bas` (pt depuis le haut) : sous l'en-tête, jamais masqué."""
    img = ImageReader(io.BytesIO(_logo_filigrane()))
    l, h = img.getSize()
    hauteur = bas - haut
    largeur = hauteur * l / h
    c.drawImage(img, (LARGEUR - largeur) / 2, HAUTEUR - bas, largeur, hauteur)


def dessiner_fond(c, haut_filigrane=196):
    """Filigrane, bordures bleues et carrés des coins : identiques sur toutes les attestations."""
    # Filigrane (logo pâle, sous l'en-tête), puis bordures bleues
    dessiner_filigrane(c, haut_filigrane)
    for nom, (x0, y0, x1, y1) in {
        'bord_haut': (38.76, 29.04, 788.88, 52.32), 'bord_bas': (34.32, 542.52, 798.0, 566.52),
        'bord_gauche': (28.32, 35.88, 47.76, 540.84), 'bord_droit': (788.76, 35.88, 810.48, 543.84),
    }.items():
        c.drawImage(_image(nom), x0, HAUTEUR - y1, x1 - x0, y1 - y0, mask='auto')
    # Carrés bleus aux 4 coins (bleu plein, contour gris, petit carré blanc au centre)
    for x0, y0, x1, y1 in [(28.32, 28.32, 56.04, 52.68), (789.0, 28.32, 813.72, 52.68),
                           (791.88, 542.16, 816.6, 566.52), (28.32, 540.72, 53.04, 565.08)]:
        c.setFillColorRGB(0.0431, 0.2902, 0.9882)
        c.setStrokeColorRGB(0.188, 0.188, 0.188)
        c.setLineWidth(1.44)
        c.rect(x0, HAUTEUR - y1, x1 - x0, y1 - y0, stroke=1, fill=1)
        cx, cy = (x0 + x1) / 2, HAUTEUR - (y0 + y1) / 2
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColorRGB(1, 1, 1)
        c.rect(cx - 3.96, cy - 3.6, 7.92, 7.2, stroke=1, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)


def dessiner_bloc_signataire(c, haut_signature, normale='Helvetica', grasse='Helvetica-Bold', signataire=None):
    """
    Sans cachet ni signature : fonction et nom du signataire en texte, aux mêmes places que sur
    l'image signature_scolarite, avec l'espace entre les deux pour signer et tamponner à la main.
    """
    from .signataires import signataire as signataire_par_defaut
    s = signataire or signataire_par_defaut()
    centre = 650.4
    c.setFillColorRGB(0, 0, 0)
    for texte, police, haut in ((s.fonction, grasse, 11.5), (s.nom, normale, 82)):
        taille = 12.5
        while stringWidth(texte, police, taille) > 300 and taille > 8:     # tient entre le centre et la bordure
            taille -= 0.5
        c.setFont(police, taille)
        c.drawCentredString(centre, HAUTEUR - haut_signature - haut, texte)


def signature_disponible():
    """
    L'image « signature et cachet » n'est pas dans Git (elle ne doit pas être publique) : elle est copiée
    à la main sur le serveur. Tant qu'elle manque, les documents s'impriment avec le nom du signataire
    et un espace vide pour signer, au lieu d'échouer.
    """
    if (_conf('dossier_images') / 'signature_scolarite.png').exists():
        return True
    import logging
    logging.getLogger('pfe').warning("Image de signature absente : %s (document imprimé sans cachet ni signature)",
                                     _conf('dossier_images') / 'signature_scolarite.png')
    return False


def dessiner_pied(c, url_verification, haut_signature=417.12, cachet_signature=True, polices=None, signataire=None):
    """
    Signature et cachet (ou leur emplacement vide), QR code de vérification (bas gauche), bas de page.
    L'image « signature et cachet » est celle de la scolarité : un chef de programme (Master) signe à la main.
    """
    if cachet_signature and (signataire is None or signataire.scolarite) and signature_disponible():
        c.drawImage(_image('signature_scolarite'), 428.4, HAUTEUR - haut_signature - 95.4, 343.92, 95.4, mask='auto')
    else:
        dessiner_bloc_signataire(c, haut_signature, *(polices or ('Helvetica', 'Helvetica-Bold')), signataire=signataire)
    if url_verification:
        dessiner_qr(c, url_verification, x=62, haut=420, taille=82)
    _centre(c, 'Cette attestation ne doit contenir ni rature, ni surcharge', 534.1, 'Times-BoldItalic', 10)


def dessiner_entete_paysage(c, largeur_bandeau=520, haut=48, signataire=None):
    """En-tête des relevés, à l'échelle de la page paysage, entre les bordures. Renvoie le haut du trait."""
    from .entete import dessiner_entete
    y = dessiner_entete(c, LARGEUR, HAUTEUR, MARGE_G, 784.44, haut=haut, largeur_bandeau=largeur_bandeau,
                        taille_service=15, taille_coordonnees=11, ecart=17, signataire=signataire)
    return HAUTEUR - (y - 4)


def dessiner_cadre(c, attestation, url_verification=None):
    """Cadre des attestations d'orientation, d'inscription et de niveau (QR de vérification compris)."""
    from .signataires import signataire_attestation
    signataire = signataire_attestation(attestation)
    dessiner_fond(c)
    # En-tête officiel : le même que sur les relevés (bandeau, service, coordonnées du chef qui signe, trait)
    dessiner_entete_paysage(c, signataire=signataire)

    # N° de référence et date
    c.setFont('Times-Roman', 12)
    y = _y(191.5, 12)
    c.drawString(MARGE_G, y, f"N° Ref : {attestation.num_ref}")
    c.drawRightString(MARGE_D, y, f"{_conf('ville')}, le {date_longue(attestation.date_delivrance)}")

    dessiner_pied(c, url_verification, signataire=signataire)


STYLE_TEXTE = ParagraphStyle('texte', fontName='Times-Roman', fontSize=14, leading=24.2,
                             alignment=TA_JUSTIFY, spaceAfter=16)
# Texte plus long (orientation) : 12 pt, comme le modèle
STYLE_TEXTE_12 = ParagraphStyle('texte12', parent=STYLE_TEXTE, fontSize=12, leading=20.8, spaceAfter=20)

INTERLIGNE_TITRE = 22.7
BAS_TEXTE = 405          # le texte s'arrête au-dessus de la signature


def placer_texte(c, paragraphes, style, x, haut, largeur, bas):
    """
    Écrit les paragraphes entre `haut` et `bas` (mesurés depuis le haut de la page).
    Le texte est MESURÉ avant d'être dessiné : s'il ne tient pas (nom ou département très
    longs), la taille est réduite pas à pas, pour ne jamais perdre un paragraphe
    (ReportLab supprime sinon le surplus sans prévenir).
    """
    hauteur = bas - haut
    taille, interligne, apres = style.fontSize, style.leading, style.spaceAfter
    while True:
        s = ParagraphStyle(f'{style.name}_{taille}', parent=style, fontSize=taille,
                           leading=interligne, spaceAfter=apres)
        blocs = [Paragraph(p, s) for p in paragraphes]
        total = sum(b.wrap(largeur, hauteur)[1] for b in blocs) + apres * (len(blocs) - 1)
        if total <= hauteur or taille <= 9:
            break
        taille, interligne, apres = taille - 0.5, interligne * (taille - 0.5) / taille, apres * 0.9
    Frame(x, HAUTEUR - bas, largeur, hauteur + 2, leftPadding=0, rightPadding=0,
          topPadding=0, bottomPadding=0).addFromList(blocs, c)


def generer_pdf(attestation, titre, paragraphes, style=STYLE_TEXTE, url_verification=None):
    """
    PDF complet : cadre commun + titre (une ou plusieurs lignes) + paragraphes
    (texte ReportLab, <b> autorisé) + QR code de vérification.
    """
    lignes_titre = [titre] if isinstance(titre, str) else list(titre)
    flux = io.BytesIO()
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(flux, pagesize=(LARGEUR, HAUTEUR))
    c.setTitle(f"{' '.join(lignes_titre).title()} – {attestation.num_ref}")
    c.setAuthor('gePed – IST de Mamou')
    dessiner_cadre(c, attestation, url_verification)
    for i, ligne in enumerate(lignes_titre):
        _centre(c, ligne, 206.1 + i * INTERLIGNE_TITRE, 'Times-Bold', 20, 720)
    haut_texte = 240 + (len(lignes_titre) - 1) * INTERLIGNE_TITRE + (4 if len(lignes_titre) > 1 else 0)
    placer_texte(c, paragraphes, style, MARGE_G + 5.6, haut_texte, MARGE_D - MARGE_G - 5.6, BAS_TEXTE)
    c.showPage()
    c.save()
    return flux.getvalue()


# ---------------------------------------------------------------- attestation d'inscription

def est_premiere_annee(licence):
    """Licence 1, 1ère Année, M1 : première inscription (sinon réinscription)."""
    premieres = {_cle(n) for n in _conf('premieres_annees')}
    return _cle(getattr(licence, 'nom', '')) in premieres


def titre_inscription(licence):
    return "ATTESTATION D'INSCRIPTION" if est_premiere_annee(licence) else "ATTESTATION DE REINSCRIPTION"


def niveau_reel(licence):
    """Niveau tel qu'il est enregistré : « LICENCE 4 », « 1ère Année », « M1 »… (jamais converti)."""
    return ' '.join((licence.nom or '').split())


def _semestres(licence):
    abreges = [s.abrege or s.nom for s in Semestre.objects.filter(licence=licence).order_by('nom')]
    if not abreges:
        return ''
    if len(abreges) == 1:
        return f"Semestre {abreges[0]}"
    return f"Semestres {', '.join(abreges[:-1])} et {abreges[-1]}"


def _nom_programme(filiere):
    return (filiere.description or '').strip() or filiere.nom.title()


def _programme_reel(filiere):
    """Nom réel du programme (la description porte souvent celui du département) : inscription, niveau,
    fin d'études."""
    return filiere.nom


def _nom_departement(filiere):
    """Département du programme (champ `departement` du Service) ; à défaut, le programme lui-même."""
    return _nom_programme(filiere.departement_effectif)


def _signataire(attestation):
    from .signataires import signataire_attestation
    return signataire_attestation(attestation)


def _echap(v):
    return str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def contenu_inscription(attestation):
    """Titre et paragraphes de l'attestation d'inscription / de réinscription."""
    e, lic = attestation.etudiant, attestation.licence
    semestres = _semestres(lic)
    niveau = (f"aux <b>{semestres}</b> ({_echap(niveau_reel(lic))})" if semestres
              else f"en <b>{_echap(niveau_reel(lic))}</b>")
    programme = (f" du Programme <b>{_echap(_programme_reel(attestation.filiere))}</b>"
                 if attestation.filiere else '')
    texte = (
        f"Je soussigné {_echap(_signataire(attestation).nom)},  {_echap(_signataire(attestation).fonction)} de {_echap(_conf('etablissement'))}, "
        f"atteste que l’étudiant·e : <b>{_echap(e.prenom.upper())} {_echap(e.nom.upper())},</b> "
        f"Matricule : <b>{_echap(e.matricule or '—')}</b>, a été inscrit·e {niveau}{programme} "
        f"au compte de l’année universitaire <b>{_echap(attestation.session.annee)}</b>."
    )
    fin = "En foi de quoi, la présente attestation lui est délivrée pour servir et valoir ce que de droit."
    return titre_inscription(lic), [texte, fin]


# ---------------------------------------------------------------- attestation d'orientation

TITRE_ORIENTATION = ["ATTESTATION DE REUSSITE A LA SELECTION ET A L'ORIENTATION DANS",
                     "LES INSTITUTS D'ENSEIGNEMENT SUPERIEUR"]


def contenu_orientation(attestation):
    """Réussite à la sélection et à l'orientation (données du bac de l'étudiant)."""
    e = attestation.etudiant
    v = lambda x: _echap(x) if x not in (None, '') else '—'                    # noqa: E731
    departement = (f", au compte du Département : <b>{_echap(_nom_programme(attestation.filiere))}</b> "
                   f"de l'Institut Supérieur de Technologie de Mamou" if attestation.filiere else '')
    texte = (
        f"Je soussigné {_echap(_signataire(attestation).nom)},  {_echap(_signataire(attestation).fonction)}  de {_echap(_conf('etablissement'))}, "
        f"atteste que l’étudiant(e) : <b>{_echap(e.prenom.upper())} {_echap(e.nom.upper())},</b> "
        f"INE : <b>{v(e.ine)}</b>, PV Bac Unique : <b>{v(e.pvbac)}</b>, Profil : <b>{v(e.option_de_bac)}</b>, "
        f"Centre : <b>{v(e.centre_de_bac)}</b>, a réussi à la sélection et à l'orientation dans les Institutions "
        f"d'Enseignement Supérieur  Session : <b>{v(e.annee_orientation)}</b>{departement}."
    )
    fin = "En foi de quoi, cette attestation lui est délivrée pour servir et valoir ce que de droit."
    return TITRE_ORIENTATION, [texte, fin], STYLE_TEXTE_12


# ---------------------------------------------------------- attestation de niveau

def contenu_niveau(attestation):
    """Cours suivis (semestres de la licence) dans une année universitaire."""
    e, lic = attestation.etudiant, attestation.licence
    semestres = _semestres(lic)
    suivi = (f"des {semestres.replace('Semestres ', 'Semestres <b>').replace('Semestre ', 'Semestre <b>')}</b> "
             f"({_echap(niveau_reel(lic))})" if semestres else f"de la <b>{_echap(niveau_reel(lic))}</b>")
    departement = (f", du Programme : <b>{_echap(_programme_reel(attestation.filiere))}</b> "
                   f"de {_echap(_conf('etablissement'))}" if attestation.filiere else '')
    texte = (
        f"Je soussigné, atteste que l’étudiant·e : <b>{_echap(e.prenom.upper())} {_echap(e.nom.upper())}</b>, "
        f"Matricule : <b>{_echap(e.matricule or '—')}</b>{departement}, a suivi les cours théoriques et "
        f"pratiques {suivi} au compte de l'année universitaire <b>{_echap(attestation.session.annee)}</b>."
    )
    fin = "En foi de quoi, la présente attestation lui est délivrée pour servir et valoir ce que de droit."
    return "ATTESTATION DE NIVEAU", [texte, fin]


CONTENUS = {
    Attestation.ORIENTATION: contenu_orientation,
    Attestation.INSCRIPTION: contenu_inscription,
    Attestation.NIVEAU: contenu_niveau,
    # Fin d'études : en-tête propre (pdf_fin_etudes), ajoutée plus bas
}


# ---------------------------------------------------------- attestation de fin d'études

BLEU_INSTITUT, BRUN_TITRE = (0, 0, 0.6), (0.6, 0.2, 0)          # #000099, #993300 (modèle)


def _polices_fin_etudes():
    """
    Century Gothic si elle est installée (police du modèle), sinon Helvetica.
    Renvoie (normale, grasse) et déclare la famille pour les <b> des paragraphes.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping
    if 'CenturyGothic' in pdfmetrics.getRegisteredFontNames():
        return 'CenturyGothic', 'CenturyGothic-Bold'
    dossiers = [r'C:\Windows\Fonts', '/usr/share/fonts/truetype/msttcorefonts', '/usr/share/fonts/TTF']
    for d in dossiers:
        try:
            pdfmetrics.registerFont(TTFont('CenturyGothic', f'{d}/GOTHIC.TTF'))
            pdfmetrics.registerFont(TTFont('CenturyGothic-Bold', f'{d}/GOTHICB.TTF'))
            addMapping('CenturyGothic', 0, 0, 'CenturyGothic')
            addMapping('CenturyGothic', 1, 0, 'CenturyGothic-Bold')
            return 'CenturyGothic', 'CenturyGothic-Bold'
        except Exception:
            continue
    return 'Helvetica', 'Helvetica-Bold'


def libelle_cycle(code, forme='entete'):
    return _conf(f'cycles_{forme}').get(code or '', '')


def contenu_fin_etudes(attestation):
    """Texte de l'attestation de fin d'études supérieures (avec le cycle)."""
    e = attestation.etudiant
    cycle = libelle_cycle(attestation.cycle, 'texte')
    departement = (f", Programme <b>{_echap(_programme_reel(attestation.filiere))}</b>" if attestation.filiere else '')
    texte = (
        f"Je soussigné {_echap(_signataire(attestation).nom)},  {_echap(_signataire(attestation).fonction)} ,  atteste  que M ou Mme :  "
        f"<b>{_echap(e.prenom.upper())} {_echap(e.nom.upper())}</b>, Matricule : <b>{_echap(e.matricule or '—')}</b>, "
        f"INE : <b>{_echap(e.ine or '—')}</b>{departement}, a effectivement suivi et validé tous les cours "
        f"théoriques et pratiques {('du <b>' + _echap(cycle) + '</b> ') if cycle else ''}de notre institution."
    )
    fin = "En foi de quoi, la présente attestation lui est délivrée pour servir et valoir ce que de droit."
    return "ATTESTATION DE FIN D'ETUDES SUPERIEURES", [texte, fin]


def pdf_fin_etudes(attestation, url_verification=None, cachet_signature=False):
    """
    En-tête solennel (modèle « HOULEY ATTESTATION ») + cycle à côté du titre + même fond et même pied.
    Par défaut SANS cachet ni signature (signés à la main) : seul un super utilisateur peut demander
    la version avec cachet et signature numérisés (voir views.attestation_pdf).
    """
    normale, grasse = _polices_fin_etudes()
    titre, paragraphes = contenu_fin_etudes(attestation)
    flux = io.BytesIO()
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(flux, pagesize=(LARGEUR, HAUTEUR))
    c.setTitle(f"{titre.title()} – {attestation.num_ref}")
    c.setAuthor('gePed – IST de Mamou')
    dessiner_fond(c, haut_filigrane=232)

    # En-tête des relevés, en plus grand (bandeau plus large : document solennel)
    from .signataires import signataire_attestation
    signataire = signataire_attestation(attestation)
    dessiner_entete_paysage(c, largeur_bandeau=675, signataire=signataire)

    # Date, cartouche de référence
    c.setFont(grasse, 12)
    c.drawRightString(MARGE_D, _y(227, 12), f"{_conf('ville')}, le {date_longue(attestation.date_delivrance)}")
    ref = f"REF: {attestation.num_ref}"
    c.setFillColorRGB(*BLEU_INSTITUT)
    c.rect(53.5, HAUTEUR - 250.3, max(155.4, stringWidth(ref, normale, 14) + 8), 22.8, stroke=0, fill=1)
    c.setFillColorRGB(0.965, 0.96, 0.96)
    c.setFont(normale, 14)
    c.drawString(57.5, _y(231.7, 14), ref)

    # Titre + cycle (en-tête)
    cycle = libelle_cycle(attestation.cycle, 'entete')
    sous_titre = f" ({cycle})" if cycle else ''
    taille = 28
    while stringWidth(titre, grasse, taille) + stringWidth(sous_titre, grasse, 10) > 720 and taille > 18:
        taille -= 1
    total = stringWidth(titre, grasse, taille) + stringWidth(sous_titre, grasse, 10)
    x = (LARGEUR - total) / 2
    c.setFillColorRGB(*BRUN_TITRE)
    c.setFont(grasse, taille)
    c.drawString(x, _y(264.7, 28), titre)
    if sous_titre:
        c.setFont(grasse, 10)
        c.drawString(x + stringWidth(titre, grasse, taille), _y(264.7, 28), sous_titre)
    c.setFillColorRGB(0, 0, 0)

    # Texte
    # (le bas du texte s'arrête au-dessus du QR code, en bas à gauche)
    style = ParagraphStyle('fin', fontName=normale, fontSize=14, leading=24.2, alignment=TA_JUSTIFY, spaceAfter=24)
    placer_texte(c, paragraphes, style, 67.2, 300, 790 - 67.2, 416)

    dessiner_pied(c, url_verification, haut_signature=436.3, cachet_signature=cachet_signature,
                  polices=(normale, grasse), signataire=signataire)
    c.showPage()
    c.save()
    return flux.getvalue()


CONTENUS[Attestation.FIN_ETUDES] = contenu_fin_etudes


def _contenu_soutenance(attestation):
    from .attestation_soutenance import contenu_soutenance
    return contenu_soutenance(attestation)


CONTENUS[Attestation.SOUTENANCE] = _contenu_soutenance


def pdf_attestation(attestation, url_verification=None, cachet_signature=False):
    """
    `cachet_signature` ne concerne que la fin d'études (les autres types sont toujours signés).
    C'est la vue qui décide : uniquement pour un super utilisateur qui le demande.
    """
    if attestation.type == Attestation.FIN_ETUDES:
        return pdf_fin_etudes(attestation, url_verification, cachet_signature=cachet_signature)
    if attestation.type == Attestation.SOUTENANCE:
        from .attestation_soutenance import pdf_soutenance
        return pdf_soutenance(attestation, url_verification)
    titre, paragraphes, *style = CONTENUS[attestation.type](attestation)
    return generer_pdf(attestation, titre, paragraphes, *style, url_verification=url_verification)


# ---------------------------------------------------------------- page publique de vérification

def titre_document(attestation):
    """Titre tel qu'imprimé sur le document."""
    titre = CONTENUS[attestation.type](attestation)[0] if attestation.type in CONTENUS else attestation.get_type_display()
    titre = titre if isinstance(titre, str) else ' '.join(titre)
    if attestation.type in (Attestation.FIN_ETUDES, Attestation.SOUTENANCE) and libelle_cycle(attestation.cycle):
        titre += f" ({libelle_cycle(attestation.cycle)})"
    return titre


def infos_publiques(attestation):
    """
    Ce que la page publique affiche : uniquement les informations IMPRIMÉES sur le document,
    pour que la personne qui scanne puisse les comparer (rien d'autre sur l'étudiant).
    """
    e = attestation.etudiant
    infos = [('Étudiant(e)', f"{e.prenom.upper()} {e.nom.upper()}"), ('Matricule', e.matricule or '—')]
    if attestation.type == Attestation.ORIENTATION:
        infos += [('INE', e.ine or '—'), ('PV Bac Unique', e.pvbac or '—'), ('Profil', e.option_de_bac or '—'),
                  ('Centre', e.centre_de_bac or '—'), ("Session d'orientation", e.annee_orientation or '—')]
        if attestation.filiere:
            infos.append(('Département', _nom_programme(attestation.filiere)))
    elif attestation.type == Attestation.FIN_ETUDES:
        infos.append(('INE', e.ine or '—'))
        if attestation.filiere:
            infos.append(('Programme', _programme_reel(attestation.filiere)))
        if attestation.cycle:
            infos.append(('Cycle validé', libelle_cycle(attestation.cycle, 'texte')))
    elif attestation.type == Attestation.SOUTENANCE:
        from .attestation_soutenance import soutenance_de
        from .notes import mention
        s = soutenance_de(attestation)
        if attestation.filiere:
            infos.append(('Département', _nom_departement(attestation.filiere)))      # imprimé sur l'attestation
            infos.append(('Programme', _nom_programme(attestation.filiere)))
        if attestation.cycle:
            infos.append(('Cycle', libelle_cycle(attestation.cycle, 'texte') or attestation.cycle))
        if attestation.session:
            infos.append(('Année universitaire', attestation.session.annee))
        if s and s.projet and s.projet.theme:
            infos.append(('Thème', s.projet.theme))
        if s and s.note_finale is not None:
            infos.append(('Mention', mention(s.note_finale) or '—'))
    else:
        if attestation.licence:
            semestres = _semestres(attestation.licence)
            niveau = niveau_reel(attestation.licence)
            infos.append(('Niveau', f"{semestres} ({niveau})" if semestres else niveau))
        if attestation.filiere:
            infos.append(('Programme', _programme_reel(attestation.filiere)))
        if attestation.session:
            infos.append(('Année universitaire', attestation.session.annee))
    return infos
