"""
Photos (étudiants et personnel) : compression, nommage et remplacement des fichiers.

La photo est d'abord recadrée (3:4) et compressée dans le navigateur (static/pfe/js/photo.js).
Le serveur applique de toute façon les mêmes règles, au cas où elle arriverait brute
(ancien navigateur, JavaScript désactivé, envoi direct) :
  - 600 × 800 px maximum, JPEG, 350 Ko maximum (qualité réduite par paliers, puis taille) ;
  - rotation corrigée d'après l'EXIF (photos de téléphone couchées) ;
  - métadonnées EXIF supprimées (elles peuvent contenir la position GPS).
L'ancienne photo est supprimée du disque quand on la remplace ou la retire.
"""
import io
import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

TAILLE_MAX_ENVOI = 10 * 1024 * 1024      # fichier brut accepté (il est réduit ensuite)
POIDS_CIBLE = 350 * 1024                 # poids maximum de la photo enregistrée
DIMENSIONS_MAX = (600, 800)              # largeur × hauteur (format identité 3:4)
QUALITES = (88, 82, 76, 70, 64, 58, 52)

MESSAGE_ILLISIBLE = "Ce fichier n'est pas une image lisible : choisissez une photo JPG, PNG ou WEBP."


def a_une_photo(obj):
    """Vraie photo (pas vide, pas l'image par défaut du modèle)."""
    return bool(obj is not None and obj.photo and 'default' not in obj.photo.name)


def url_photo(obj):
    return obj.photo.url if a_une_photo(obj) else None


def _en_rgb(img):
    """JPEG n'a pas de transparence : on pose l'image sur un fond blanc."""
    if img.mode == 'RGB':
        return img
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
        fond = Image.new('RGB', img.size, (255, 255, 255))
        fond.paste(img, mask=img.split()[-1])
        return fond
    return img.convert('RGB')


def compresser(fichier):
    """Renvoie les octets JPEG (≤ 350 Ko, ≤ 600 × 800, sans EXIF) ou lève ValidationError."""
    taille = getattr(fichier, 'size', None)
    if taille and taille > TAILLE_MAX_ENVOI:
        raise ValidationError(f"Photo trop lourde ({taille / 1048576:.1f} Mo) : 10 Mo maximum.")
    if hasattr(fichier, 'seek'):
        fichier.seek(0)
    brut = fichier.read()
    try:
        img = Image.open(io.BytesIO(brut))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise ValidationError(MESSAGE_ILLISIBLE)

    # Déjà conforme (ex. photo préparée par le navigateur) : gardée telle quelle, sans recompression
    if (img.format == 'JPEG' and len(brut) <= POIDS_CIBLE and not img.info.get('exif')
            and img.width <= DIMENSIONS_MAX[0] and img.height <= DIMENSIONS_MAX[1]):
        return brut

    img = _en_rgb(ImageOps.exif_transpose(img))
    img.thumbnail(DIMENSIONS_MAX, Image.LANCZOS)
    while True:
        for qualite in QUALITES:
            flux = io.BytesIO()
            img.save(flux, 'JPEG', quality=qualite, optimize=True, progressive=True)   # sans exif=
            if flux.tell() <= POIDS_CIBLE:
                return flux.getvalue()
        # Image très chargée : on réduit encore la taille (rare à 600 × 800)
        img = img.resize((max(1, int(img.width * .85)), max(1, int(img.height * .85))), Image.LANCZOS)


def _nom_fichier(base):
    base = unicodedata.normalize('NFKD', str(base or 'photo')).encode('ascii', 'ignore').decode()
    base = re.sub(r'[^A-Za-z0-9]+', '-', base).strip('-').lower() or 'photo'
    return f"{base[:40]}_{timezone.now():%Y%m%d%H%M%S}.jpg"


def supprimer_fichier(nom):
    if nom and 'default' not in nom and default_storage.exists(nom):
        default_storage.delete(nom)


def enregistrer_photo(obj, contenu, base_nom):
    """Enregistre `contenu` (octets JPEG compressés) comme photo de `obj` et supprime l'ancienne."""
    ancienne = obj.photo.name if a_une_photo(obj) else None
    obj.photo.save(_nom_fichier(base_nom), ContentFile(contenu), save=False)
    obj.save(update_fields=['photo'])
    supprimer_fichier(ancienne)


def retirer_photo(obj):
    ancienne = obj.photo.name if a_une_photo(obj) else None
    obj.photo = None
    obj.save(update_fields=['photo'])
    supprimer_fichier(ancienne)
