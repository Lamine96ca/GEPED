"""
Salles et bâtiments : import d'une liste de salles (Excel ou CSV, avec mode simulation), modèle et exports.

Colonnes : batiment, salle, capacite (obligatoires) ; type, equipements, disponible (facultatives).
Une salle est reconnue par son bâtiment et son nom : déjà enregistrée, elle est mise à jour. Un bâtiment
absent est créé. Une ligne en erreur est signalée sans bloquer les autres ; en simulation, rien n'est enregistré.
"""
import csv
import io
import unicodedata

from django.db import transaction

from .importer import lire_lignes, texte
from .models import Batiment, Salle

OBLIGATOIRES = ['batiment', 'salle', 'capacite']
COLONNES = OBLIGATOIRES + ['type', 'equipements', 'disponible']
ALIAS = {'bâtiment': 'batiment', 'batiments': 'batiment', 'nom': 'salle', 'nom_salle': 'salle', 'numero': 'salle',
         'salles': 'salle', 'capacité': 'capacite', 'places': 'capacite', 'nombre_de_places': 'capacite',
         'effectif': 'capacite', 'equipement': 'equipements', 'type_de_salle': 'type'}
EN_TETES = ['Bâtiment', 'Salle', 'Type', 'Capacité', 'Équipements', 'Disponible']


def _cle(v):
    v = unicodedata.normalize('NFKD', texte(v)).encode('ascii', 'ignore').decode().lower()
    return ' '.join(v.split())


def en_type(v):
    """« Amphithéâtre », « amphi », « TP », « labo », « Salle informatique »… → code du type ; vide → salle de cours."""
    k = _cle(v)
    if not k:
        return Salle.COURS
    for code, libelle in Salle.TYPES:
        if k in (code, _cle(libelle)):
            return code
    for mot, code in (('amphi', Salle.AMPHI), ('labo', Salle.LABO), ('tp', Salle.LABO), ('informat', Salle.INFO),
                      ('atelier', Salle.ATELIER), ('cours', Salle.COURS), ('classe', Salle.COURS)):
        if mot in k:
            return code
    raise ValueError(f"type inconnu « {texte(v)} » (salle de cours, amphithéâtre, laboratoire, salle informatique, "
                     f"atelier)")


def en_capacite(v):
    t = texte(v).replace(' ', '').replace(',', '.')
    try:
        n = float(t)
    except ValueError:
        raise ValueError(f"capacité « {texte(v)} » : un nombre de places est attendu")
    if n != int(n) or not 1 <= n <= 5000:
        raise ValueError(f"capacité « {texte(v)} » : un nombre entier de places, de 1 à 5000")
    return int(n)


def en_disponible(v):
    k = _cle(v)
    if k in ('', 'oui', 'o', 'yes', 'y', '1', 'vrai', 'true', 'x', 'disponible'):
        return True
    if k in ('non', 'n', 'no', '0', 'faux', 'false', 'indisponible'):
        return False
    raise ValueError(f"disponible « {texte(v)} » : oui ou non")


class RapportSalles:
    def __init__(self, simulation):
        self.simulation = simulation
        self.lignes = self.creees = self.mises_a_jour = self.inchangees = 0
        self.erreurs = []
        self.colonnes_ignorees = []
        self.batiments_crees = []
        self.reparations = 0

    @property
    def nb_erreurs(self):
        return len(self.erreurs)


def importer_salles(fichier, simulation=False):
    rapport = RapportSalles(simulation)
    entetes, lignes = lire_lignes(fichier)
    rapport.reparations = getattr(fichier, 'gped_reparations', 0)
    entetes = [ALIAS.get(e, e) for e in entetes]
    rapport.colonnes_ignorees = [e for e in entetes if e and e not in COLONNES]
    manquantes = [c for c in OBLIGATOIRES if c not in entetes]
    if manquantes:
        raise ValueError("Colonnes obligatoires manquantes : " + ", ".join(f"« {c} »" for c in manquantes) + ".")
    with transaction.atomic():
        batiments = {_cle(b.nom): b for b in Batiment.objects.all()}
        salles = {(s.batiment_id, _cle(s.nom)): s for s in Salle.objects.all()}
        vues = {}
        for numero, brut in enumerate(lignes, start=2):
            if brut is None or all(texte(c) == '' for c in brut):
                continue
            rapport.lignes += 1
            ligne = {entetes[i]: brut[i] for i in range(min(len(entetes), len(brut))) if entetes[i]}
            try:
                vides = [c for c in OBLIGATOIRES if not texte(ligne.get(c))]
                if vides:
                    raise ValueError("valeur manquante : " + ", ".join(vides))
                nom_bat = ' '.join(texte(ligne['batiment']).split())[:100]
                nom_salle = ' '.join(texte(ligne['salle']).split())[:60]
                d = {'capacite': en_capacite(ligne['capacite']),
                     'type': en_type(ligne.get('type')) if 'type' in ligne else None,
                     'equipements': ' '.join(texte(ligne.get('equipements')).split())[:200] if 'equipements' in ligne
                     else None,
                     'disponible': en_disponible(ligne.get('disponible')) if 'disponible' in ligne else None}
                cle_bat = _cle(nom_bat)
                if (cle_bat, _cle(nom_salle)) in vues:
                    raise ValueError(f"salle répétée dans le fichier (déjà à la ligne {vues[(cle_bat, _cle(nom_salle))]})")
                vues[(cle_bat, _cle(nom_salle))] = numero
            except ValueError as e:
                rapport.erreurs.append((numero, str(e)))
                continue
            bat = batiments.get(cle_bat)
            if bat is None:
                bat = batiments[cle_bat] = Batiment.objects.create(nom=nom_bat)
                rapport.batiments_crees.append(nom_bat)
            s = salles.get((bat.pk, _cle(nom_salle)))
            if s is None:
                Salle.objects.create(batiment=bat, nom=nom_salle, capacite=d['capacite'],
                                     type=d['type'] or Salle.COURS, equipements=d['equipements'] or '',
                                     disponible=True if d['disponible'] is None else d['disponible'])
                rapport.creees += 1
                continue
            avant = (s.capacite, s.type, s.equipements, s.disponible)
            s.capacite = d['capacite']
            for champ in ('type', 'equipements', 'disponible'):        # absente du fichier : on garde l'existant
                if d[champ] is not None:
                    setattr(s, champ, d[champ])
            if (s.capacite, s.type, s.equipements, s.disponible) != avant:
                s.save()
                rapport.mises_a_jour += 1
            else:
                rapport.inchangees += 1
        if simulation:
            transaction.set_rollback(True)
    return rapport


def modele_salles_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Salles'
    ws.append(COLONNES)
    ws.append(['Bâtiment A', 'Salle 12', 45, 'Salle de cours', 'Vidéoprojecteur', 'oui'])
    ws.append(['Bâtiment A', 'Amphi 1', 200, 'Amphithéâtre', 'Sonorisation, vidéoprojecteur', ''])
    ws.append(['Bâtiment B', 'Labo de chimie', 30, 'Laboratoire', '', 'oui'])
    for cell in ws[1]:
        obligatoire = cell.value in OBLIGATOIRES
        cell.font = Font(bold=True, color='1F2937' if obligatoire else 'FFFFFF')
        cell.fill = PatternFill('solid', fgColor='FBBD08' if obligatoire else '41627C')
    for col, largeur in zip('ABCDEF', (18, 18, 11, 20, 32, 12)):
        ws.column_dimensions[col].width = largeur
    ws.freeze_panes = 'A2'
    flux = io.BytesIO()
    wb.save(flux)
    return flux.getvalue()


def _rangs(salles):
    return [[s.batiment.nom, s.nom, s.get_type_display(), s.capacite, s.equipements,
             'oui' if s.disponible else 'non'] for s in salles]


def export_xlsx(salles):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Salles'
    ws.append(EN_TETES)
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='41627C')
    for r in _rangs(salles):
        ws.append(r)
    for col, largeur in zip('ABCDEF', (24, 22, 20, 11, 36, 12)):
        ws.column_dimensions[col].width = largeur
    flux = io.BytesIO()
    wb.save(flux)
    return flux.getvalue()


def export_csv(salles):
    flux = io.StringIO()
    w = csv.writer(flux, delimiter=';')
    w.writerow(EN_TETES)
    w.writerows(_rangs(salles))
    return ('﻿' + flux.getvalue()).encode('utf-8')          # BOM : Excel lit bien les accents
