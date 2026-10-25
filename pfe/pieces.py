"""
Pièces du dossier étudiant : acte de naissance, diplôme et relevé du bac, relevé de notes…

Chaque pièce est rangée en UN PDF compressé, dans le stockage privé (prive/dossiers/AAAA/),
jamais servi par le site : seules la scolarité (fiche de l'étudiant) et les vues prévues y accèdent.

Deux façons de la déposer :
  - un PDF déjà prêt (scanner de bureau) : accepté tel quel s'il fait 3 Mo au plus ;
  - une ou plusieurs photos (scan avec la caméra du téléphone ou du PC, fichiers JPG/PNG/WEBP) :
    chaque page est redressée (EXIF), réduite à 1 700 px (≈ 200 ppp sur une page A4), compressée
    en JPEG, puis toutes les pages sont réunies dans un seul PDF (≈ 150 à 300 Ko par page).
Le navigateur réduit déjà les photos avant l'envoi (static/pfe/js/gped-pieces.js) : moins de
données sur une connexion lente ; le serveur applique de toute façon les mêmes règles.
Une nouvelle pièce du même type (et de la même session) remplace l'ancienne, sauf « Autre pièce ».
"""
import io
import re

from django.core.exceptions import ValidationError
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from .photos import _en_rgb

PDF_MAX = 3 * 1024 * 1024                 # PDF envoyé tel quel
IMAGE_MAX = 15 * 1024 * 1024              # photo brute (elle est réduite ensuite)
PAGES_MAX = 10
COTE_MAX = 1700                           # pixels, plus grand côté d'une page
PIXELS_MAX = 50_000_000                   # au-delà : image refusée avant décodage (image piégée, « bombe »)
QUALITE_JPEG = 72
EXTENSIONS_IMAGES = ('.jpg', '.jpeg', '.png', '.webp')


def _est_pdf(f):
    debut = f.read(5)
    f.seek(0)
    return debut == b'%PDF-'


def _nb_pages_pdf(octets):
    return max(1, len(re.findall(rb'/Type\s*/Page(?!s)', octets)))


def _page_jpeg(f):
    """Photo d'une page → JPEG réduit (octets), largeur, hauteur."""
    if f.size > IMAGE_MAX:
        raise ValidationError(f"« {f.name} » est trop lourd ({f.size / 1048576:.1f} Mo) : 15 Mo au plus par photo.")
    try:
        img = Image.open(f)
        if img.width * img.height > PIXELS_MAX:
            raise ValidationError(f"« {f.name} » : image trop grande ({img.width} × {img.height} pixels).")
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError(f"« {f.name} » n'est pas une image lisible : JPG, PNG ou WEBP, ou un PDF.")
    img = _en_rgb(ImageOps.exif_transpose(img))
    img.thumbnail((COTE_MAX, COTE_MAX), Image.LANCZOS)
    flux = io.BytesIO()
    img.save(flux, 'JPEG', quality=QUALITE_JPEG, optimize=True, progressive=True)
    return flux.getvalue(), img.width, img.height


def _pdf_des_pages(pages):
    """Pages JPEG → un PDF : chaque page sur une feuille A4 (portrait ou paysage selon la photo)."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas
    flux = io.BytesIO()
    c = Canvas(flux, pagesize=A4)
    c.setAuthor('gePed – IST de Mamou')
    for octets, l, h in pages:
        format_page = landscape(A4) if l > h else A4
        c.setPageSize(format_page)
        marge = 14
        larg_max, haut_max = format_page[0] - 2 * marge, format_page[1] - 2 * marge
        echelle = min(larg_max / l, haut_max / h)
        lp, hp = l * echelle, h * echelle
        c.drawImage(ImageReader(io.BytesIO(octets)), (format_page[0] - lp) / 2, (format_page[1] - hp) / 2, lp, hp)
        c.showPage()
    c.save()
    return flux.getvalue()


def preparer(fichiers):
    """
    Fichiers envoyés (un PDF, ou des photos de pages) → (octets du PDF, nombre de pages).
    Lève ValidationError avec un message clair.
    """
    fichiers = [f for f in fichiers if f]
    if not fichiers:
        raise ValidationError("Ajoutez la pièce : un PDF, ou une ou plusieurs photos des pages.")
    pdfs = [f for f in fichiers if _est_pdf(f)]
    if pdfs:
        if len(fichiers) > 1:
            raise ValidationError("Un PDF s'envoie seul : pour plusieurs pages, envoyez un seul PDF ou des photos.")
        f = pdfs[0]
        if f.size > PDF_MAX:
            raise ValidationError(f"PDF trop lourd ({f.size / 1048576:.1f} Mo) : 3 Mo au plus. Scannez en noir et "
                                  f"blanc ou en 200 ppp, ou prenez les pages en photo.")
        octets = f.read()
        return octets, _nb_pages_pdf(octets)
    if len(fichiers) > PAGES_MAX:
        raise ValidationError(f"{PAGES_MAX} pages au plus par pièce.")
    for f in fichiers:
        if not str(f.name).lower().endswith(EXTENSIONS_IMAGES) and not (getattr(f, 'content_type', '') or '').startswith('image/'):
            raise ValidationError(f"« {f.name} » : seuls les PDF et les images (JPG, PNG, WEBP) sont acceptés.")
    pages = [_page_jpeg(f) for f in fichiers]
    return _pdf_des_pages(pages), len(pages)


def enregistrer(etudiant, type_piece, fichiers, utilisateur=None, par_etudiant=False, session=None, libelle=''):
    """Prépare la pièce, la range dans le stockage privé et remplace l'ancienne du même type."""
    from .models import PieceDossier, stockage_prive
    octets, pages = preparer(fichiers)
    from django.core.files.base import ContentFile
    reference = re.sub(r'[^A-Za-z0-9]+', '', etudiant.matricule or '') or f"etudiant{etudiant.pk}"
    nom = stockage_prive().save(f"dossiers/{timezone.now():%Y}/{reference}_{type_piece}_{timezone.now():%Y%m%d%H%M%S}.pdf",
                                ContentFile(octets))
    if type_piece != PieceDossier.AUTRE:
        for ancienne in PieceDossier.objects.filter(etudiant=etudiant, type=type_piece, session=session):
            supprimer(ancienne)
    return PieceDossier.objects.create(
        etudiant=etudiant, type=type_piece, libelle=libelle[:120], session=session, fichier=nom, pages=pages,
        taille=len(octets), par_etudiant=par_etudiant, utilisateur=utilisateur if getattr(utilisateur, 'pk', None) else None)


def supprimer(piece):
    """Supprime la pièce et son fichier."""
    from .models import stockage_prive
    nom = piece.fichier.name
    piece.delete()
    if nom:
        stockage = stockage_prive()
        if stockage.exists(nom):
            stockage.delete(nom)


def ouvrir(piece):
    from .models import stockage_prive
    return stockage_prive().open(piece.fichier.name, 'rb')
