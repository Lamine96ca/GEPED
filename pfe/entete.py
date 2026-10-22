"""
En-tête officiel commun aux documents PDF (relevés de notes, fiche d'inscription, attestations) :
bandeau de l'établissement (emblème du ministère, « IST Mamou », logo), « SERVICE SCOLARITÉ »,
coordonnées (boîte postale, e-mail, téléphone) et trait bleu.
"""
import io
from functools import lru_cache

from django.conf import settings
from reportlab.lib.utils import ImageReader

BLEU_TRAIT = (0.12, 0.25, 0.75)
RAPPORT_BANDEAU = 905 / 4020                  # hauteur / largeur de l'image du bandeau
MARGE_BAS_BANDEAU = 0.033                     # l'image a déjà une marge blanche en bas (part de sa hauteur)


@lru_cache(maxsize=1)
def _bandeau_allege():
    """Bandeau réduit à 1600 px (≈ 215 ppp imprimé) : 68 Ko au lieu de 146, pour des PDF légers."""
    from PIL import Image
    with Image.open(settings.BASE_DIR / 'ressources' / 'releve' / 'entete.jpg') as img:
        img = img.convert('RGB')
        img = img.resize((1600, round(1600 * img.height / img.width)), Image.LANCZOS)
    flux = io.BytesIO()
    img.save(flux, 'JPEG', quality=80, optimize=True)
    return flux.getvalue()


def bandeau():
    return ImageReader(io.BytesIO(_bandeau_allege()))


def dessiner_entete(c, largeur_page, hauteur_page, g, d, haut=14, largeur_bandeau=None, taille_service=12,
                    taille_coordonnees=8.5, ecart=16):
    """
    Dessine l'en-tête, bandeau centré sur la page, son haut à `haut` pt du haut de la page.
    Les coordonnées et le trait vont de `g` à `d`. Renvoie la ligne de base des coordonnées
    (le trait est 4 pt plus bas). Le bandeau est opaque : le filigrane doit commencer sous l'en-tête.
    """
    conf = settings.GPED_ATTESTATION
    largeur = largeur_bandeau or (d - g)
    hauteur = largeur * RAPPORT_BANDEAU
    y = hauteur_page - haut - hauteur
    c.drawImage(bandeau(), (largeur_page - largeur) / 2, y, largeur, hauteur)
    y += hauteur * MARGE_BAS_BANDEAU
    c.setFillColorRGB(0, 0, 0)
    c.setFont('Helvetica-Bold', taille_service)
    c.drawCentredString(largeur_page / 2, y, 'SERVICE SCOLARITÉ')
    y -= ecart
    c.setFont('Helvetica-Bold', taille_coordonnees)
    c.drawString(g, y, conf['boite_postale'])
    c.drawCentredString((g + d) / 2, y, f"Email : {conf['email']}")
    c.drawRightString(d, y, f"Tél : {conf['telephone']}")
    c.setStrokeColorRGB(*BLEU_TRAIT)
    c.setLineWidth(1.6)
    c.line(g, y - 4, d, y - 4)
    c.setStrokeColorRGB(0, 0, 0)
    return y
