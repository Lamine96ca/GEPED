"""
Fiche d'inscription (A4 portrait, ReportLab), à imprimer après l'inscription.

Reprend le modèle « FICHE INSCRIPTION.pdf » de la scolarité, en version légère (texte, traits,
pas d'image de fond) : en-tête de l'établissement, cadre de la photo à gauche du titre (photo de
l'étudiant si elle existe, sinon cadre vide à coller), puis les rubriques sur des lignes.
Titre : « FICHE D'INSCRIPTION » en première année (L1, 1ère Année…), sinon « FICHE DE RÉINSCRIPTION ».
Les cases Boursier / Non boursier / Formation payante et Initiale / Continue sont cochées d'après la
fiche de l'étudiant ; sans réponse, elles restent vides, à cocher à la main.
"""
import io

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .attestations import est_premiere_annee
from .entete import dessiner_entete
from .photos import a_une_photo

LARGEUR, HAUTEUR = A4                       # 595 x 842 pt
G, D = 42, LARGEUR - 42                     # marges gauche et droite
BLEU = (0.255, 0.384, 0.486)                # #41627c


def titre_fiche(licence):
    return "FICHE D'INSCRIPTION" if est_premiere_annee(licence) else "FICHE DE RÉINSCRIPTION"


def _photo_allegee(etudiant):
    """Photo réduite (240 × 320, JPEG) pour garder un PDF léger ; None s'il n'y en a pas."""
    if not a_une_photo(etudiant):
        return None
    try:
        from PIL import Image
        with etudiant.photo.open('rb') as f:
            img = Image.open(f)
            img.load()
        img = img.convert('RGB')
        img.thumbnail((240, 320))
        flux = io.BytesIO()
        img.save(flux, 'JPEG', quality=80, optimize=True)
        flux.seek(0)
        return ImageReader(flux)
    except Exception:
        return None                          # photo illisible : cadre vide


def _texte_ajuste(c, x, y, texte, largeur_max, police='Times-Roman', taille=12):
    while stringWidth(texte, police, taille) > largeur_max and taille > 7:
        taille -= 0.5
    c.setFont(police, taille)
    c.drawString(x, y, texte)


def _champ(c, x, y, libelle, valeur, fin):
    """« Libellé : valeur » sur une ligne qui va jusqu'à `fin`."""
    c.setFont('Helvetica-BoldOblique', 9)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(x, y, libelle)
    debut = x + stringWidth(libelle, 'Helvetica-BoldOblique', 9) + 6
    c.setLineWidth(0.5)
    c.setStrokeColorRGB(0.45, 0.45, 0.45)
    c.line(debut, y - 2.5, fin, y - 2.5)
    _texte_ajuste(c, debut + 4, y + 1, str(valeur or ''), fin - debut - 8)


def _case(c, x, y, libelle, largeur_libelle, cochee=False):
    c.setFont('Helvetica-BoldOblique', 9)
    c.drawString(x, y, libelle)
    c.setLineWidth(1)
    c.setStrokeColorRGB(0, 0, 0)
    x0 = x + largeur_libelle
    c.rect(x0, y - 3, 13, 13)
    if cochee:                                   # coche (✓) tracée : aucune police spéciale nécessaire
        c.setLineWidth(1.8)
        c.setLineCap(1)
        c.setLineJoin(1)
        chemin = c.beginPath()
        chemin.moveTo(x0 + 2.6, y + 3.2)
        chemin.lineTo(x0 + 5.4, y + 0.2)
        chemin.lineTo(x0 + 10.6, y + 7.6)
        c.drawPath(chemin, stroke=1, fill=0)
        c.setLineWidth(1)
        c.setLineCap(0)
        c.setLineJoin(0)


def pdf_fiche_inscription(inscription):
    e, conf = inscription.etudiant, settings.GPED_ATTESTATION
    flux = io.BytesIO()
    c = Canvas(flux, pagesize=A4)
    titre = titre_fiche(inscription.licence)
    c.setTitle(f"{titre.title()} – {e.prenom} {e.nom}")
    c.setAuthor('gePed – IST de Mamou')

    # En-tête : le même que sur les relevés de notes
    bas = dessiner_entete(c, LARGEUR, HAUTEUR, G, D) - 4
    c.setLineWidth(0.8)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)

    # Photo à gauche (90 × 120 pt, format 3:4 ≈ 3,2 × 4,2 cm) et titre à côté
    larg_photo, haut_photo = 90, 120
    p_haut = bas - 18
    p_bas = p_haut - haut_photo
    c.rect(G, p_bas, larg_photo, haut_photo)
    photo = _photo_allegee(e)
    if photo:
        c.drawImage(photo, G + 3, p_bas + 3, larg_photo - 6, haut_photo - 6, preserveAspectRatio=True, anchor='c')
    else:
        c.setFont('Helvetica', 8)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawCentredString(G + larg_photo / 2, p_bas + haut_photo / 2 + 4, "Photo")
        c.drawCentredString(G + larg_photo / 2, p_bas + haut_photo / 2 - 7, "d'identité")
        c.setFillColorRGB(0, 0, 0)
    c.setFont('Times-Bold', 19)
    c.setFillColorRGB(*BLEU)
    c.drawCentredString((G + larg_photo + D) / 2, p_bas + haut_photo / 2 - 6, titre)
    c.setFillColorRGB(0, 0, 0)
    bas = p_bas + 58                                      # les rubriques commencent sous la photo

    # Rubriques
    milieu = G + (D - G) * 0.55
    sexe = {'M': 'HOMME', 'F': 'FEMME'}.get(e.sexe, '')
    tel = ' / '.join(x for x in (f"Tél étudiant : {e.telephone}" if e.telephone else '',
                                 f"Tél parent : {e.contact_de_tuteur}" if e.contact_de_tuteur else '') if x)
    programme = inscription.filiere.nom if inscription.filiere else ''
    niveau = inscription.licence.nom + (f" · {inscription.semestre.nom}" if inscription.semestre else '')
    y = bas - 82
    pas = 24
    lignes = [
        [('Département :', programme, D)],
        [('PROGRAMME :', programme, D)],
        [('Année :', inscription.session.annee, milieu - 12), ('Niveau :', niveau, D)],
        [('Matricule :', e.matricule, milieu - 12), ('Promotion :', inscription.promotion or '', D)],
        [('Nom :', e.nom.upper(), D)],
        [('Prénoms :', e.prenom.upper(), D)],
        [('Date de naissance :', e.date_de_naissance.strftime('%d/%m/%Y') if e.date_de_naissance else '', milieu - 12),
         ('Lieu de naissance :', (e.lieu_de_naissance or '').upper(), D)],
        [('Nationalité :', (e.nationalite or '').upper(), milieu - 12), ('Sexe :', sexe, D)],
        [('Filiation : Père :', (e.pere or '').upper(), D)],
        [('Mère :', (e.mere or '').upper(), D)],
    ]
    for ligne in lignes:
        x = G
        for libelle, valeur, fin in ligne:
            _champ(c, x, y, libelle, valeur, fin)
            x = milieu
        y -= pas

    # Cases : cochées d'après la fiche de l'étudiant, sinon laissées à cocher à la main
    _case(c, G, y, 'Boursier', 70, e.boursier == 'boursier')
    _case(c, G + 175, y, 'Non boursier', 70, e.boursier == 'non_boursier')
    _case(c, G + 350, y, 'Formation payante', 105, e.boursier == 'payante')
    y -= pas
    c.setFont('Helvetica-BoldOblique', 9)
    c.drawString(G, y, 'Type de formation :')
    _case(c, G + 175, y, 'Initiale', 70, e.type_formation == 'initiale')
    _case(c, G + 350, y, 'Continue', 105, e.type_formation == 'continue')
    y -= pas

    for ligne in [
        [('PV Bac :', e.pvbac, milieu - 12), ('Centre :', (e.centre_de_bac or '').upper(), D)],
        [('PV Sélection / INE :', e.ine, D)],
        [('Adresse :', e.adresse, D)],
        [('Numéro de téléphone :', tel, D)],
    ]:
        x = G
        for libelle, valeur, fin in ligne:
            _champ(c, x, y, libelle, valeur, fin)
            x = milieu
        y -= pas

    # Date et signatures
    y -= 6
    date = inscription.date_inscription
    c.setFont('Helvetica-BoldOblique', 9.5)
    c.drawRightString(D, y, f"{conf['ville']}, le {date:%d/%m/%Y}" if date else f"{conf['ville']}, le ……………")
    y -= 30
    c.setFont('Helvetica-BoldOblique', 9.5)
    c.drawString(G + 10, y, "L'ÉTUDIANT(E)")
    c.drawRightString(D - 10, y, conf['fonction'].upper())
    c.setFont('Helvetica', 9.5)
    c.drawRightString(D - 10, y - 70, conf['signataire'])

    # Bas de page
    c.setFont('Helvetica', 7)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(G, 26, 'gePed · IST de Mamou')
    c.drawRightString(D, 26, f"{e.matricule or ''} · {inscription.session.annee}")
    c.showPage()
    c.save()
    return flux.getvalue()
