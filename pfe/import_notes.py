"""
Import des notes (Excel .xlsx ou CSV) : scolarité et chefs de programme. Barème sur 10.

Une ligne = une note d'un étudiant dans une matière. Colonnes repérées par leur nom :
  - obligatoires : matricule, matiere (nom ou code), session ;
  - facultatives : programme (si besoin de préciser), controle, tp, examen, moyenne, rattrapage.
Il faut au moins une note sur la ligne. « ABS » = 0 (un absent a 0).
La colonne moyenne est la moyenne déposée par le programme : elle est prise telle quelle ;
sinon la moyenne est calculée (voir pfe/notes.py). Si la moyenne est remplie et que contrôle,
TP et examen valent tous 0, ces 0 sont considérés comme vides (cases remplies par défaut).

La matière est cherchée parmi celles de l'inscription de l'étudiant dans cette session
(son programme et sa licence). Contrôles : étudiant inscrit (pas préinscrit), matière trouvée,
notes dans le barème. Une note déjà enregistrée est MISE À JOUR (et la modification tracée) ;
une ligne répétée dans le fichier est ignorée (doublon). Un semestre déjà délibéré est refusé :
seule la scolarité peut encore modifier ces notes, depuis la saisie, avec un motif.
Chef de programme : si une seule ligne concerne un autre programme, TOUT l'import est refusé.
"""
import io
import re
from collections import defaultdict

from django.db import transaction

from .import_matieres import ImportRefuse
from .importer import _index_programmes, cle, cle_session, lire_lignes, texte
from .models import Etudiant, Inscription, Matiere, Note, Session
from .notes import CHAMPS_SAISIE, en_note, enregistrer_notes, recalculer, saisie_de, semestres_delibres

OBLIGATOIRES = ['matricule', 'matiere', 'session']
COLONNES = OBLIGATOIRES + ['programme'] + list(CHAMPS_SAISIE)
ALIAS = {
    'matieres': 'matiere', 'nom_matiere': 'matiere', 'code_matiere': 'matiere', 'intitule': 'matiere',
    'cc': 'controle', 'controle_continu': 'controle', 'devoir': 'controle',
    'travaux_pratiques': 'tp', 'exam': 'examen', 'examen_final': 'examen',
    'moy': 'moyenne', 'moyenne_matiere': 'moyenne', 'rattrapages': 'rattrapage', 'note_rattrapage': 'rattrapage',
}


def cle_intitule(texte_brut):
    """Intitulé sans casse, accents ni ponctuation : « Probabilité_Statistique » = « Probabilité Statistique »."""
    return ' '.join(re.sub(r'[^A-Z0-9]+', ' ', cle(texte_brut)).split())


class RapportNotes:
    def __init__(self):
        self.lignes = 0
        self.creees = 0
        self.mises_a_jour = 0
        self.inchangees = 0
        self.erreurs = []
        self.doublons = []
        self.simulation = False
        self.colonnes_ignorees = []
        self.reparations = 0

    @property
    def nb_erreurs(self):
        return len(self.erreurs)

    @property
    def nb_doublons(self):
        return len(self.doublons)

    def erreur(self, ligne, message):
        self.erreurs.append((ligne, message))


def importer_notes(fichier, profil, utilisateur=None, simulation=False):
    rapport = RapportNotes()
    rapport.simulation = simulation
    entetes, lignes = lire_lignes(fichier)
    rapport.reparations = getattr(fichier, 'gped_reparations', 0)
    entetes = [ALIAS.get(e, e) for e in entetes]
    rapport.colonnes_ignorees = [e for e in entetes if e and e not in COLONNES]
    manquantes = [c for c in OBLIGATOIRES if c not in entetes]
    if manquantes:
        raise ValueError("Colonnes obligatoires manquantes : " + ", ".join(f"« {c} »" for c in manquantes) + ".")
    if not any(c in entetes for c in CHAMPS_SAISIE):
        raise ValueError("Aucune colonne de note : ajoutez au moins « moyenne », « controle », « tp », « examen » "
                         "ou « rattrapage ».")

    autorises = None if profil.est_scolarite else set(profil.programme_ids)
    programmes = _index_programmes()
    sessions = {cle_session(s.annee): s for s in Session.objects.all()}

    # 1) Lecture des lignes ---------------------------------------------------
    brutes = []
    for numero, brut in enumerate(lignes, start=2):
        if brut is None or all(texte(c) == '' for c in brut):
            continue
        rapport.lignes += 1
        ligne = {entetes[i]: brut[i] for i in range(min(len(entetes), len(brut))) if entetes[i]}
        try:
            vides = [c for c in OBLIGATOIRES if not texte(ligne.get(c))]
            if vides:
                raise ValueError("valeur manquante : " + ", ".join(vides))
            session = sessions.get(cle_session(ligne['session']))
            if session is None:
                raise ValueError(f"session inconnue « {texte(ligne['session'])} »")
            programme = None
            if texte(ligne.get('programme')):
                programme = programmes.get(cle(ligne['programme']))
                if programme is None:
                    raise ValueError(f"programme inconnu « {texte(ligne['programme'])} »")
            valeurs = {}
            for c in CHAMPS_SAISIE:
                try:
                    valeurs[c] = en_note(ligne.get(c))
                except ValueError as e:
                    raise ValueError(f"{c} : {e}")
            if all(v is None for v in valeurs.values()):
                raise ValueError("aucune note sur la ligne")
            # Moyenne déposée avec contrôle, TP et examen à 0 : ces 0 ne sont que des cases remplies
            # par défaut dans le fichier → considérés comme vides
            composantes = [valeurs[c] for c in ('controle', 'tp', 'examen') if valeurs[c] is not None]
            if valeurs['moyenne'] is not None and composantes and all(v == 0 for v in composantes):
                valeurs.update(controle=None, tp=None, examen=None)
        except ValueError as e:
            rapport.erreur(numero, str(e))
            continue
        brutes.append((numero, texte(ligne['matricule']), session, programme, texte(ligne['matiere']), valeurs))

    # 2) Étudiants, inscriptions et matières concernés --------------------------
    matricules = list({b[1] for b in brutes})
    etudiants = {}
    for i in range(0, len(matricules), 500):
        etudiants.update({e.matricule: e for e in Etudiant.objects.filter(matricule__in=matricules[i:i + 500])})
    inscriptions = defaultdict(list)                       # (etudiant, session) -> [inscriptions]
    ids = [e.pk for e in etudiants.values()]
    for i in range(0, len(ids), 500):
        for insc in Inscription.objects.filter(etudiant_id__in=ids[i:i + 500], filiere__isnull=False):
            inscriptions[(insc.etudiant_id, insc.session_id)].append(insc)
    triplets = {(x.filiere_id, x.session_id, x.licence_id) for lst in inscriptions.values() for x in lst}
    matieres = defaultdict(dict)                           # triplet -> {clé nom/code: matière}
    if triplets:
        for m in Matiere.objects.filter(filiere_id__in={t[0] for t in triplets},
                                        session_id__in={t[1] for t in triplets},
                                        licence_id__in={t[2] for t in triplets}).select_related('filiere', 'semestre'):
            t = (m.filiere_id, m.session_id, m.licence_id)
            if t in triplets:
                matieres[t][cle_intitule(m.nom)] = m
                if m.code:
                    matieres[t].setdefault(cle_intitule(m.code), m)

    valides, vues, hors_perimetre = [], {}, []
    for numero, matricule, session, programme, nom_matiere, valeurs in brutes:
        etu = etudiants.get(matricule)
        if etu is None:
            rapport.erreur(numero, f"matricule inconnu « {matricule} »")
            continue
        if etu.preinscrit:
            rapport.erreur(numero, f"{matricule} est préinscrit : finalisez d'abord son inscription")
            continue
        candidates = [x for x in inscriptions.get((etu.pk, session.pk), [])
                      if programme is None or x.filiere_id == programme.pk]
        if not candidates:
            rapport.erreur(numero, f"{matricule} n'est pas inscrit en {session}"
                                   + (f" en {programme}" if programme else ''))
            continue
        intitule = cle_intitule(nom_matiere)
        matiere = next((matieres[(x.filiere_id, x.session_id, x.licence_id)][intitule]
                        for x in candidates if intitule in matieres[(x.filiere_id, x.session_id, x.licence_id)]), None)
        if matiere is None:
            rapport.erreur(numero, f"matière « {nom_matiere} » introuvable pour {matricule} "
                                   f"(son programme et sa licence en {session})")
            continue
        if autorises is not None and matiere.filiere_id not in autorises:
            hors_perimetre.append((numero, matiere.filiere.nom))
            continue
        identite = (etu.pk, matiere.pk)
        if identite in vues:
            rapport.doublons.append((numero, f"{matricule} · {matiere.nom} : déjà présente à la ligne {vues[identite]} du fichier"))
            continue
        vues[identite] = numero
        valides.append((numero, etu, matiere, valeurs))

    if hors_perimetre:
        details = ', '.join(f"ligne {n} ({p})" for n, p in hors_perimetre[:10])
        if len(hors_perimetre) > 10:
            details += f"… et {len(hors_perimetre) - 10} autre(s)"
        raise ImportRefuse(f"Import refusé : le fichier contient des notes d'un programme qui n'est pas le vôtre : "
                           f"{details}. Aucune note n'a été enregistrée.")

    delibres = semestres_delibres([m for _, _, m, _ in valides])
    retenues = []
    for numero, etu, matiere, valeurs in valides:
        if (matiere.filiere_id, matiere.session_id, matiere.semestre_id) in delibres:
            rapport.erreur(numero, f"{matiere.nom} ({matiere.semestre.nom}) est déjà délibérée : modification "
                                   f"seulement par la scolarité, depuis la saisie, avec un motif")
            continue
        retenues.append((etu, matiere, valeurs))

    # 3) Écriture : création ou mise à jour, puis calcul des résultats ------------
    if retenues:
        with transaction.atomic():
            existantes = {}
            paires_ids = {(e.pk, m.pk) for e, m, _ in retenues}
            for n in Note.objects.filter(etudiant_id__in={p[0] for p in paires_ids},
                                         matiere_id__in={p[1] for p in paires_ids}):
                existantes[(n.etudiant_id, n.matiere_id)] = n
            changements = []
            for etu, matiere, valeurs in retenues:
                note = existantes.get((etu.pk, matiere.pk))
                if note is not None:
                    # Colonnes absentes du fichier : on garde ce qui était saisi
                    valeurs = {c: valeurs[c] if c in entetes else saisie_de(note)[c] for c in CHAMPS_SAISIE}
                    if saisie_de(note) == valeurs:
                        rapport.inchangees += 1
                        continue
                changements.append((note, etu.pk, matiere, valeurs))
            rapport.creees, rapport.mises_a_jour = enregistrer_notes(
                changements, utilisateur, motif=f"Import du fichier {getattr(fichier, 'name', '')}".strip())
            recalculer({etu.pk for etu, _, _ in retenues})
            if simulation:
                transaction.set_rollback(True)
    rapport.erreurs.sort()
    rapport.doublons.sort()
    return rapport


def modele_notes_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Notes'
    ws.append(COLONNES)
    ws.append(['2304000123', 'Algorithmique et programmation', '2025 - 2026', '', 6, 7.5, 6.5, '', ''])
    ws.append(['2304000217', 'Algorithmique et programmation', '2025 - 2026', '', '', '', '', 6.75, ''])
    ws.append(['2304000328', 'INF101', '2025 - 2026', '', 3, '', 'ABS', '', 6])
    for cell in ws[1]:
        obligatoire = cell.value in OBLIGATOIRES
        cell.font = Font(bold=True, color='1F2937' if obligatoire else 'FFFFFF')
        cell.fill = PatternFill('solid', fgColor='FBBD08' if obligatoire else '41627C')
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(12, max(len(str(c.value or '')) for c in col) + 2)
    ws.freeze_panes = 'A2'
    flux = io.BytesIO()
    wb.save(flux)
    return flux.getvalue()
