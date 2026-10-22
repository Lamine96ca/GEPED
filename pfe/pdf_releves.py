"""
PDF des relevés de notes (A4 portrait, ReportLab), d'après les modèles de la scolarité :
bandeau officiel (emblème du ministère, IST Mamou, logo), filigrane, identité de l'étudiant,
QR code de vérification, tableau des matières (crédits, note chiffrée, notation, observation).

  - Relevé semestriel / annuel : un tableau, moyenne, crédits validés et mention.
  - Extrait du livret : un bloc par année (deux colonnes), tout sur UNE page (taille adaptée),
    moyenne du cycle, note de soutenance, moyenne générale et mention.

« PROVISOIRE » en filigrane tant qu'un semestre n'est pas délibéré. Sans cachet ni signature
par défaut : seul un super utilisateur peut demander la version avec cachet et signature.
"""
import io
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .attestations import _image, _nom_programme, signature_disponible
from .entete import dessiner_entete

LARGEUR, HAUTEUR = A4
G, D = 30, LARGEUR - 30
BLEU = (0.12, 0.25, 0.75)
GRIS_TETE = (0.88, 0.89, 0.91)
GRIS_TEXTE = (0.35, 0.35, 0.38)
MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
        'novembre', 'décembre']


def _ressource(nom):
    return ImageReader(str(settings.BASE_DIR / 'ressources' / 'releve' / nom))


def fmt(v):
    return '—' if v is None else f"{v:.2f}".replace('.', ',')


def _date(d):
    return f"{d.day:02d} {MOIS[d.month - 1]} {d.year}"


def _ajuste(texte, police, taille, largeur):
    """Réduit la taille (puis coupe avec « … ») pour que le texte tienne dans `largeur`."""
    while stringWidth(texte, police, taille) > largeur and taille > 5.5:
        taille -= 0.25
    while stringWidth(texte, police, taille) > largeur and len(texte) > 3:
        texte = texte[:-2] + '…'
    return texte, taille


def _texte(c, x, y, texte, police, taille, largeur=None, align='g'):
    if largeur:
        texte, taille = _ajuste(texte, police, taille, largeur)
    c.setFont(police, taille)
    {'g': c.drawString, 'c': c.drawCentredString, 'd': c.drawRightString}[align](x, y, texte)


def _entete(c, releve, contenu, url_verification):
    conf = settings.GPED_ATTESTATION
    c.drawImage(_ressource('filigrane.jpg'), 0, 0, LARGEUR, HAUTEUR)
    y = dessiner_entete(c, LARGEUR, HAUTEUR, G, D)          # le logo du filigrane commence sous l'en-tête
    compact = releve.type == 'E'                         # extrait : plus de place pour les tableaux
    y -= 15
    c.setFont('Helvetica-Bold', 9)
    c.drawString(G, y, f"N° Réf : {releve.num_ref}")
    c.setFont('Helvetica', 9)
    c.drawRightString(D, y, f"{conf['ville']}, le {_date(releve.date_delivrance)}")
    y -= 24
    _texte(c, LARGEUR / 2, y, contenu['titre'], 'Helvetica-Bold', 16, align='c')
    if not contenu['definitif']:
        c.setFillColorRGB(0.75, 0.1, 0.1)
        _texte(c, LARGEUR / 2, y - 12, 'Document provisoire : notes non encore délibérées', 'Helvetica-Oblique', 8,
               align='c')
        c.setFillColorRGB(0, 0, 0)
    # Identité
    e = releve.etudiant
    programme = _nom_programme(releve.filiere)
    lignes = [('Prénoms et Nom :', f"{e.prenom.upper()} {e.nom.upper()}"), ('Matricule :', e.matricule or '—')]
    if e.ine:
        lignes.append(('INE :', e.ine))
    lignes += [('Département :', programme), ('Programme :', programme)]
    if contenu.get('session'):
        lignes.append(('Année universitaire :', contenu['session']))
    y -= 26 if compact else 30
    haut_identite = y + 11
    pas = 13.5 if compact else 15.5
    for libelle, valeur in lignes:
        c.setFillColorRGB(*GRIS_TEXTE)
        _texte(c, G + 18, y, libelle, 'Helvetica', 9.5 if compact else 10)
        c.setFillColorRGB(0, 0, 0)
        _texte(c, G + 130, y, valeur, 'Times-Bold', 10.5 if compact else 11, largeur=D - G - 130 - 100)
        y -= pas
    if contenu.get('niveau'):
        texte, taille = _ajuste(contenu['niveau'], 'Times-Bold', 10.5, 150)
        c.setFont('Times-Bold', taille)
        c.drawRightString(D, y + pas, texte)
        c.setFont('Helvetica', 9.5)
        c.setFillColorRGB(*GRIS_TEXTE)
        c.drawRightString(D - stringWidth(texte, 'Times-Bold', taille) - 4, y + pas, 'Niveau :')
        c.setFillColorRGB(0, 0, 0)
    if url_verification:
        _qr(c, url_verification, D - 62, haut_identite - 58, 58)
    # Le bloc d'identité ne descend pas plus bas que le QR code (et sa mention)
    return min(y + pas - 12, haut_identite - 58 - 14) if compact else y - 6


def _qr(c, url, x, y, taille):
    """QR code de vérification (page publique), coin bas gauche en (x, y), avec sa mention."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    qr = QrCodeWidget(url, barLevel='M', barBorder=1)
    x0, y0, x1, y1 = qr.getBounds()
    dessin = Drawing(taille, taille, transform=[taille / (x1 - x0), 0, 0, taille / (y1 - y0), 0, 0])
    dessin.add(qr)
    c.setFillColorRGB(1, 1, 1)
    c.rect(x - 2, y - 2, taille + 4, taille + 4, stroke=0, fill=1)
    renderPDF.draw(dessin, c, x, y)
    c.setFillColorRGB(*GRIS_TEXTE)
    c.setFont('Helvetica', 6.5)
    c.drawCentredString(x + taille / 2, y - 9, 'Scannez pour vérifier')
    c.setFillColorRGB(0, 0, 0)


def _provisoire(c):
    c.saveState()
    c.setFillColorRGB(0.8, 0.1, 0.1)
    c.setFillAlpha(0.13)
    c.translate(LARGEUR / 2, HAUTEUR / 2 - 40)
    c.rotate(35)
    c.setFont('Helvetica-Bold', 92)
    c.drawCentredString(0, 0, 'PROVISOIRE')
    c.restoreState()


BAS_CADRE = 40                       # ligne bleue du bas de page : rien ne descend en dessous


def _pied(c, releve, cachet_signature, y_mention, extrait=False):
    """
    Signature à DROITE du bloc des moyennes : son titre commence au niveau de la ligne « Mention ».
    Elle reste toujours dans le cadre, au-dessus de la ligne du bas de page.
    """
    conf = settings.GPED_ATTESTATION
    y = max(y_mention, BAS_CADRE + 80)
    x = D - 115
    if cachet_signature and signature_disponible():
        largeur = 225
        hauteur = largeur * 95.4 / 343.92
        c.drawImage(_image('signature_scolarite'), x - largeur / 2, y - hauteur + 10, largeur, hauteur, mask='auto')
    else:
        _texte(c, x, y, conf['fonction'], 'Helvetica-Bold', 10, align='c')
        _texte(c, x, y - 62, conf['signataire'], 'Helvetica', 10, align='c')
    c.setStrokeColorRGB(*BLEU)
    c.setLineWidth(1.6)
    c.line(G + 70, BAS_CADRE, D - 70, BAS_CADRE)
    c.setFillColorRGB(0, 0, 0)
    _texte(c, LARGEUR / 2, BAS_CADRE - 12,f"{'Cet extrait' if extrait else 'Ce relevé'} ne doit comporter ni surcharge ni rature",
           'Helvetica-Oblique', 8, align='c')


# ---------------------------------------------------------------- relevé semestriel / annuel

def _tableau_releve(c, b, y, annuel):
    colonnes = [('N°', 22, 'c'), ('Matières', None, 'g')]
    if annuel:
        colonnes.append(('Sem.', 32, 'c'))
    colonnes += [('Crédits', 40, 'c'), ('Note /10', 46, 'c'), ('Notation', 46, 'c'), ('Observation', 92, 'c')]
    fixe = sum(w for _, w, _ in colonnes if w)
    colonnes = [(t, w or (D - G - fixe), a) for t, w, a in colonnes]
    lignes = b['lignes']
    disponible = y - 205                                   # place pour moyennes + signature (côte à côte)
    hauteur = max(11, min(16, (disponible - 22) / max(1, len(lignes) + 1)))
    taille = min(9.5, hauteur * 0.62)
    # En-tête
    c.setFillColorRGB(*GRIS_TETE)
    c.rect(G, y - 20, D - G, 20, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    x = G
    for titre, w, _ in colonnes:
        _texte(c, x + w / 2, y - 13.5, titre, 'Times-Bold', 10, align='c')
        x += w
    c.setStrokeColorRGB(0.55, 0.55, 0.58)
    c.setLineWidth(0.5)
    haut_tableau = y
    y -= 20
    for i, l in enumerate(lignes, start=1):
        valeurs = [str(i), l['matiere']] + ([l['semestre']] if annuel else []) + [
            str(l['credit']), fmt(l['note']), l['lettre'] or '—', l['observation']]
        x = G
        for (titre, w, align), v in zip(colonnes, valeurs):
            police = 'Times-Bold' if titre in ('Matières', 'Note /10') else 'Times-Roman'
            if titre == 'Observation':
                c.setFillColorRGB(*((0.75, 0.1, 0.1) if not l['validee'] else (0.1, 0.45, 0.2)))
                police = 'Helvetica'
            pos = x + 4 if align == 'g' else x + w / 2
            _texte(c, pos, y - hauteur + hauteur * 0.32, v, police, taille if titre != 'Observation' else taille - 1,
                   largeur=w - 8, align=align)
            c.setFillColorRGB(0, 0, 0)
            x += w
        y -= hauteur
        c.line(G, y, D, y)
    # Total des crédits
    c.setFillColorRGB(0.96, 0.96, 0.97)
    c.rect(G, y - hauteur, D - G, hauteur, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    x_credits = G + sum(w for t, w, _ in colonnes[:colonnes.index(next(col for col in colonnes if col[0] == 'Crédits'))])
    _texte(c, x_credits - 6, y - hauteur * 0.68, 'Total des crédits', 'Times-Bold', taille, align='d')
    _texte(c, x_credits + 20, y - hauteur * 0.68, str(b['credits_total']), 'Times-Bold', taille, align='c')
    y -= hauteur
    c.line(G, y, D, y)
    # Grille verticale
    x = G
    c.rect(G, y, D - G, haut_tableau - y, stroke=1, fill=0)
    for _, w, _ in colonnes[:-1]:
        x += w
        c.line(x, haut_tableau, x, y + hauteur)
    return y


def _resume(c, y, lignes, pas=16, police=('Helvetica', 'Times-Bold'), couleur=GRIS_TEXTE):
    """Bloc « Moyenne… / Crédits validés / Mention » sous le tableau, à gauche."""
    y -= 20
    for libelle, valeur in lignes:
        c.setFillColorRGB(*couleur)
        _texte(c, G + 8, y, libelle, police[0], 10)
        c.setFillColorRGB(0, 0, 0)
        _texte(c, G + 135, y, valeur, police[1], 11.5, largeur=D - G - 135 - 240)
        y -= pas
    return y + pas                                       # hauteur de la dernière ligne (« Mention »)


def _pdf_releve(c, releve, contenu, url, cachet_signature):
    from .models import ReleveNote
    y = _entete(c, releve, contenu, url)
    b = contenu['blocs'][0]
    y = _tableau_releve(c, b, y - 8, annuel=releve.type == ReleveNote.ANNUEL)
    y_mention = _resume(c, y, ([('Notes manquantes', str(b['notes_manquantes']))] if b['notes_manquantes'] else [])
                        + [(contenu['libelle_moyenne'], f"{fmt(contenu['moyenne'])}/10"),
                           ('Crédits validés', f"{b['credits_valides']} / {b['credits_total']}"),
                           ('Mention', contenu['mention'] or '—')])
    _pied(c, releve, cachet_signature, y_mention)


# ---------------------------------------------------------------- extrait du livret

# Couleurs de l'ancien extrait de la scolarité
GRIS_BLOC = (0.86, 0.86, 0.86)        # bandeau « SESSION … | LICENCE … »
BORDEAUX = (0.5, 0.06, 0.06)          # « Moyenne Annuelle »
TRAIT = (0.25, 0.25, 0.25)            # quadrillage


def _pdf_extrait(c, releve, contenu, url, cachet_signature):
    y = _entete(c, releve, contenu, url)
    blocs = contenu['blocs']
    largeur_col = (D - G) / 2
    rangees = [blocs[i:i + 2] for i in range(0, len(blocs), 2)]
    disponible = y - 205                                   # moyennes + signature (côte à côte) en dessous
    tete, pied = 15, 13
    total_lignes = sum(max(len(b['lignes']) for b in r) + 1 for r in rangees) or 1
    hauteur = max(7, min(13.5, (disponible - len(rangees) * (tete + pied)) / total_lignes))
    taille = min(8.5, hauteur * 0.7)
    colonnes = [('Matières', largeur_col - 30 - 22 - 34 - 34, 'g'), ('Notes', 30, 'c'), ('Cr.', 22, 'c'),
                ('Notations', 34, 'c'), ('Semestres', 34, 'c')]
    c.setStrokeColorRGB(*TRAIT)
    for rangee in rangees:
        nb = max(len(b['lignes']) for b in rangee)
        bas_bloc = y - tete - hauteur * (nb + 1) - pied
        for k, b in enumerate(rangee):
            x0 = G + k * largeur_col
            insc = b['inscription']
            c.setFillColorRGB(*GRIS_BLOC)
            c.rect(x0, y - tete, largeur_col, tete, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)
            _texte(c, x0 + largeur_col / 2, y - tete + 4.2,
                   f"SESSION : {insc.session.annee} |  {insc.licence.nom.upper()}", 'Times-Bold', 9.5,
                   largeur=largeur_col - 8, align='c')
            yy = y - tete
            x = x0
            for titre, w, align in colonnes:
                _texte(c, x + w / 2, yy - hauteur * 0.72, titre, 'Helvetica-Bold' if titre in ('Matières', 'Notes')
                       else 'Helvetica', taille - 1, largeur=w - 3, align='c')
                x += w
            yy -= hauteur
            c.setLineWidth(0.4)
            c.line(x0, yy, x0 + largeur_col, yy)
            for l in b['lignes']:
                x = x0
                for (titre, w, align), v in zip(colonnes, [l['matiere'], fmt(l['note']), str(l['credit']),
                                                           l['lettre'] or '—', l['semestre']]):
                    _texte(c, x + (3 if align == 'g' else w / 2), yy - hauteur * 0.74, v,
                           'Helvetica-Bold' if titre in ('Matières', 'Notes') else 'Helvetica', taille,
                           largeur=w - 5, align=align)
                    x += w
                yy -= hauteur
                c.line(x0, yy, x0 + largeur_col, yy)
            # Colonnes et cadre du bloc
            x = x0
            for _, w, _ in colonnes[:-1]:
                x += w
                c.line(x, y - tete, x, y - tete - hauteur * (len(b['lignes']) + 1))
            c.setFillColorRGB(*BORDEAUX)
            _texte(c, x0 + colonnes[0][1] / 2, bas_bloc + 4, 'Moyenne Annuelle', 'Helvetica-Bold', 8, align='c')
            _texte(c, x0 + colonnes[0][1] + (largeur_col - colonnes[0][1]) / 2, bas_bloc + 4,
                   f"{fmt(b['moyenne'])}/10 · {b['credits_valides']}/{b['credits_total']} cr.", 'Helvetica-Bold', 8,
                   align='c')
            c.setFillColorRGB(0, 0, 0)
            c.setLineWidth(0.6)
            c.line(x0 + colonnes[0][1], bas_bloc, x0 + colonnes[0][1], bas_bloc + pied)
            c.rect(x0, bas_bloc, largeur_col, y - bas_bloc, stroke=1, fill=0)
        y = bas_bloc
    lignes = [('Moyenne du cycle', f"{fmt(contenu['moyenne_cycle'])}/10")]
    if contenu['soutenance'] is not None:
        lignes.append(('Moyenne de soutenance', f"{fmt(contenu['soutenance'])}/10"
                       + (' (Session)' if contenu['soutenance_session'] else '')))
    lignes += [('Moyenne générale', f"{fmt(contenu['moyenne'])}/10"),
               ('Crédits validés', f"{contenu['credits_valides']} / {contenu['credits_total']}"),
               ('Mention', contenu['mention'] or '—')]
    y_mention = _resume(c, y + 4, lignes, pas=14.5, police=('Times-Roman', 'Times-Bold'), couleur=(0, 0, 0))
    if not blocs:
        _texte(c, LARGEUR / 2, y + 40, "Aucune note enregistrée pour ce programme.", 'Helvetica-Oblique', 10, align='c')
    _pied(c, releve, cachet_signature, y_mention, extrait=True)


def pdf_releve(releve, contenu, url_verification=None, cachet_signature=False):
    from .models import ReleveNote
    flux = io.BytesIO()
    c = Canvas(flux, pagesize=A4)
    e = releve.etudiant
    c.setTitle(f"{contenu['titre'].title()} – {e.prenom} {e.nom} – {releve.num_ref}")
    c.setAuthor('gePed – IST de Mamou')
    if releve.type == ReleveNote.EXTRAIT:
        _pdf_extrait(c, releve, contenu, url_verification, cachet_signature)
    else:
        _pdf_releve(c, releve, contenu, url_verification, cachet_signature)
    if not contenu['definitif']:
        _provisoire(c)
    c.showPage()
    c.save()
    return flux.getvalue()
