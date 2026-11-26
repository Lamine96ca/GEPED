"""
Calcul des notes et des résultats (barème sur 10). Règles réglables : settings.GPED_NOTES.

Matière  : moyenne déposée par le programme, ou calculée avec les notes données :
             contrôle 25 % + TP 35 % + examen 40 % ;
             sans TP : contrôle 40 % + examen 60 % ;
             autres cas incomplets : mêmes poids, ramenés aux seules notes données.
           Rattrapage : on garde la meilleure des deux (mention « Session »). Absent = 0.
           Validée à partir de 5/10.
Semestre : moyenne des matières du semestre (programme, session, licence de l'inscription),
           pondérée par les crédits ; une matière sans note compte 0 (elle est signalée).
Année    : même calcul sur toutes les matières de l'année (licence).
Cycle    : PAR PROGRAMME (un ingénieur a un résultat pour le Tronc commun et un autre pour son
           programme d'orientation) : moyenne des années du programme (licence redoublée : la
           dernière année seulement) ; moyenne générale = cycle × 0,87 + soutenance × 0,13 quand
           le programme a une note de soutenance (la meilleure de la note et de la note de
           session, voir pfe/soutenances.py), sinon la moyenne du cycle.
Moyennes : tronquées à 2 décimales (jamais arrondies au-dessus).
Mentions : 5 Passable, 6 Assez bien, 7 Bien, 8 et plus Très bien.

Règlements particuliers (settings.GPED_NOTES['reglements']) : une note suit le règlement de l'inscription
de l'étudiant (cycle, session) ; il est gardé dans Note.reglement. Arrêté 877 (Tronc commun et cycle
Ingénieur depuis 2025-2026) : matière = CC 40 % + CT 60 % (CC = contrôle, TP compris ; CT = examen) ;
rattrapage retenu plafonné à 5 ; lettres A+ … E ; mentions Excellent, Remarquable, Satisfaisant,
Acceptable ; cycle = moyenne générale CUMULATIVE pondérée par les crédits, par programme (CPC pour le
Tronc commun, cycle ingénieur pour le programme d'orientation), sans soutenance ajoutée à part ;
note < 5 d'un semestre délibéré = dette.

Les résultats sont enregistrés dans Resultat par recalculer(), à appeler après une saisie,
un import ou une soutenance (pour les seuls étudiants concernés).
"""
import math
from collections import defaultdict

from django.conf import settings
from django.db import transaction


CHAMPS_SAISIE = ('controle', 'tp', 'examen', 'moyenne', 'rattrapage')


def regles(code=''):
    """
    Règles de calcul : celles de settings.GPED_NOTES, remplacées par celles du règlement `code`
    (ex. « 877 ») quand il y en a un. `code` : '' = règles générales.
    """
    base = settings.GPED_NOTES
    r = {k: v for k, v in base.items() if k != 'reglements'}
    if code:
        r.update(base.get('reglements', {}).get(code, {}))
    r['code'] = code
    return r


def code_reglement(cycle, annee, debut_cycle=None):
    """
    Règlement d'une inscription : son cycle et l'année de sa session (« 2025 - 2026 »). '' sinon.
    Règlement « premiere_inscription » (arrêté 879, art. 89) : c'est l'année de la PREMIÈRE inscription de
    l'étudiant dans ce cycle (`debut_cycle`, « 2026 - 2027 ») qui compte ; les étudiants déjà inscrits avant
    gardent les anciennes règles jusqu'au bout.
    """
    for code, r in settings.GPED_NOTES.get('reglements', {}).items():
        if cycle not in r.get('cycles', ()):
            continue
        annee_ref = (debut_cycle or annee) if r.get('premiere_inscription') else annee
        if _debut(annee_ref) >= r.get('premiere_session', 0):
            return code
    return ''


def _cycles_premiere_inscription():
    return {c for r in settings.GPED_NOTES.get('reglements', {}).values() if r.get('premiere_inscription')
            for c in r.get('cycles', ())}


def debuts_cycle(etudiant_ids):
    """{(etudiant_id, cycle): année de la première inscription dans ce cycle} (cycles concernés seulement)."""
    from .models import Inscription
    cycles, debuts = _cycles_premiere_inscription(), {}
    if not cycles or not etudiant_ids:
        return debuts
    for etu, cycle, annee in Inscription.objects.filter(etudiant_id__in=set(etudiant_ids), cycle__in=cycles) \
            .values_list('etudiant_id', 'cycle', 'session__annee'):
        if (etu, cycle) not in debuts or _debut(annee) < _debut(debuts[(etu, cycle)]):
            debuts[(etu, cycle)] = annee
    return debuts


def code_inscription(inscription, debuts=None):
    """Règlement d'une inscription ; `debuts` : résultat de debuts_cycle() déjà calculé (sinon : une requête)."""
    debut = None
    if inscription.cycle in _cycles_premiere_inscription():
        if debuts is None:
            debuts = debuts_cycle([inscription.etudiant_id])
        debut = debuts.get((inscription.etudiant_id, inscription.cycle))
    return code_reglement(inscription.cycle, inscription.session.annee, debut)


def code_soutenance(soutenance, debuts=None):
    """Règlement d'une soutenance : celui du cycle de l'étudiant pour la session de la soutenance."""
    debut = None
    if soutenance.cycle in _cycles_premiere_inscription():
        if debuts is None:
            debuts = debuts_cycle([soutenance.etudiant_id])
        debut = debuts.get((soutenance.etudiant_id, soutenance.cycle))
    return code_reglement(soutenance.cycle, soutenance.session.annee, debut)


def codes_soutenances(soutenances):
    """Calcule d'un coup le règlement de chaque soutenance (pour leur mention) ; renvoie la liste."""
    soutenances = list(soutenances)
    debuts = debuts_cycle({s.etudiant_id for s in soutenances})
    for s in soutenances:
        s._reglement = code_soutenance(s, debuts)
    return soutenances


def codes_inscriptions(inscriptions):
    """{inscription.pk: code} pour une liste d'inscriptions (avec leur session), en une requête."""
    inscriptions = list(inscriptions)
    debuts = debuts_cycle({i.etudiant_id for i in inscriptions})
    return {i.pk: code_inscription(i, debuts) for i in inscriptions}


def codes_reglement(paires):
    """[(etudiant_id, matière)] → {(etudiant_id, matiere_id): code} d'après l'inscription de l'étudiant
    dans la session, le programme et le niveau de la matière ('' sans inscription)."""
    from .models import Inscription
    paires = list(paires)
    if not paires or not settings.GPED_NOTES.get('reglements'):
        return {}
    matieres = [m for _, m in paires]
    par_cle = {}
    debuts = debuts_cycle({e for e, _ in paires})
    for etu, ses, fil, lic, cycle, annee in Inscription.objects.filter(
            etudiant_id__in={e for e, _ in paires}, session_id__in={m.session_id for m in matieres},
            filiere_id__in={m.filiere_id for m in matieres}, licence_id__in={m.licence_id for m in matieres}
    ).values_list('etudiant_id', 'session_id', 'filiere_id', 'licence_id', 'cycle', 'session__annee'):
        par_cle[(etu, ses, fil, lic)] = par_cle.get((etu, ses, fil, lic)) or code_reglement(
            cycle, annee, debuts.get((etu, cycle)))
    return {(e, m.pk): par_cle.get((e, m.session_id, m.filiere_id, m.licence_id), '') for e, m in paires}


def reglement_de_note(note):
    return codes_reglement([(note.etudiant_id, note.matiere)]).get((note.etudiant_id, note.matiere_id), '')


def en_note(valeur):
    """« 6,5 » → 6.5 ; vide → None ; « ABS » → 0 (un absent a 0). Lève ValueError hors barème."""
    s = str(valeur if valeur is not None else '').strip().replace(',', '.')
    if s == '':
        return None
    if s.upper() in ('ABS', 'ABSENT', 'A'):
        return 0.0
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"note invalide « {valeur} »")
    if not 0 <= v <= regles()['note_max']:
        raise ValueError(f"note hors barème « {valeur} » (de 0 à {regles()['note_max']})")
    return arrondi(v)                       # deux décimales, TRONQUÉES (6,257 → 6,25), jamais arrondies


# ---------------------------------------------------------------- saisie, droits, verrouillage

def saisie_de(note):
    """Ce qui a été saisi pour la note (la moyenne seulement si elle a été déposée)."""
    if note is None:
        return {c: None for c in CHAMPS_SAISIE}
    return {'controle': note.controle, 'tp': note.tp, 'examen': note.examen, 'rattrapage': note.rattrapage,
            'moyenne': note.moyenne if note.moyenne_deposee else None}


def _etat(note):
    return {**saisie_de(note), 'moyenne_calculee': note.moyenne, 'moyenne_finale': note.moyenne_finale,
            'session': note.par_session}


def appliquer_saisie(note, valeurs):
    """valeurs : les 5 champs (None = vide). Une moyenne fournie est « déposée » (pas recalculée)."""
    for c in ('controle', 'tp', 'examen', 'rattrapage'):
        setattr(note, c, valeurs.get(c))
    note.moyenne_deposee = valeurs.get('moyenne') is not None
    note.moyenne = valeurs.get('moyenne')
    note.calculer()


# ---------------------------------------------------------------- promotion d'une note
# Une note n'a pas de promotion à elle : c'est celle de l'inscription de l'étudiant dans la session,
# le programme et le niveau de la matière (une seule par étudiant, session, programme et niveau).

def _inscription_de_la_note(prefixe=''):
    from django.db.models import OuterRef
    from .models import Inscription
    return Inscription.objects.filter(etudiant_id=OuterRef(f'{prefixe}etudiant_id'),
                                      session_id=OuterRef(f'{prefixe}matiere__session_id'),
                                      filiere_id=OuterRef(f'{prefixe}matiere__filiere_id'),
                                      licence_id=OuterRef(f'{prefixe}matiere__licence_id'))


def avec_promotion(qs, prefixe=''):
    """Ajoute `promotion_nom` (et `promotion_id_note`) aux notes ; `prefixe` : 'note__' pour l'historique."""
    from django.db.models import Subquery
    from django.db.models.functions import Length
    inscription = _inscription_de_la_note(prefixe).order_by('pk')
    return qs.annotate(promotion_nom=Subquery(inscription.values('promotion__nom')[:1]),
                       promotion_id_note=Subquery(inscription.values('promotion_id')[:1])
                       ).annotate(promotion_long=Length('promotion_nom'))    # tri naturel : P2 avant P10


def filtrer_promotion(qs, promotion_id, prefixe=''):
    """Notes dont l'étudiant était inscrit dans cette promotion (session, programme et niveau de la matière)."""
    from django.db.models import Exists
    return qs.filter(Exists(_inscription_de_la_note(prefixe).filter(promotion_id=promotion_id)))


def semestres_delibres(matieres):
    """{(programme, session, semestre)} délibérés parmi ceux des matières données."""
    from .models import Deliberation
    cles = {(m.filiere_id, m.session_id, m.semestre_id) for m in matieres}
    if not cles:
        return set()
    return {cle for cle in Deliberation.objects.filter(
        filiere_id__in={c[0] for c in cles}, session_id__in={c[1] for c in cles},
        semestre_id__in={c[2] for c in cles}).values_list('filiere_id', 'session_id', 'semestre_id') if cle in cles}


def est_delibere(matiere):
    return (matiere.filiere_id, matiere.session_id, matiere.semestre_id) in semestres_delibres([matiere])


def semestres_valides(matieres):
    """{(programme, session, semestre)} validés par le chef de département parmi ceux des matières données."""
    from .models import ValidationSemestre
    cles = {(m.filiere_id, m.session_id, m.semestre_id) for m in matieres}
    if not cles:
        return set()
    return {cle for cle in ValidationSemestre.objects.filter(
        filiere_id__in={c[0] for c in cles}, session_id__in={c[1] for c in cles},
        semestre_id__in={c[2] for c in cles}).values_list('filiere_id', 'session_id', 'semestre_id') if cle in cles}


def est_valide(matiere):
    return (matiere.filiere_id, matiere.session_id, matiere.semestre_id) in semestres_valides([matiere])


TAILLE_MAX_PDF = 10 * 1024 * 1024


# Le super utilisateur modifie les notes sans motif ni justificatif : l'historique garde cette mention
MOTIF_SUPER_UTILISATEUR = "Modification par le super utilisateur (sans justificatif)"


def verifier_pdf(fichier, quoi='Le justificatif'):
    """Justificatif ou fiche de l'enseignant : un vrai PDF (pas un fichier renommé), 10 Mo au plus."""
    if not fichier:
        raise ValueError(f"{quoi} (PDF) est obligatoire.")
    if fichier.size > TAILLE_MAX_PDF:
        raise ValueError(f"{quoi} dépasse 10 Mo : scannez-le en noir et blanc ou en résolution plus basse.")
    debut = fichier.read(5)
    fichier.seek(0)
    if not str(fichier.name).lower().endswith('.pdf') or debut != b'%PDF-':
        raise ValueError(f"{quoi} doit être un fichier PDF.")
    return fichier


def _debut(annee):
    """« 2025 - 2026 » → 2025 (première année de la session)."""
    import re
    m = re.match(r'\s*(\d{4})', str(annee or ''))
    return int(m.group(1)) if m else 0


def session_ecoulee(session):
    """Session antérieure à la session en cours : ses notes sont toutes considérées délibérées."""
    from .models import Session
    courante = Session.objects.filter(en_cours=True).order_by('-annee').first() or Session.objects.order_by('-annee').first()
    return bool(session and courante and _debut(session.annee) < _debut(courante.annee))


def autorisations_actives(matieres):
    """{(programme, session, licence)} ouverts aujourd'hui par une autorisation du chef de la scolarité."""
    from django.utils import timezone
    from .models import AutorisationNotes
    cles = {(m.filiere_id, m.session_id, m.licence_id) for m in matieres}
    if not cles:
        return set()
    aujourd_hui = timezone.localdate()
    ouvertes = set()
    qs = (AutorisationNotes.objects.filter(session_id__in={c[1] for c in cles}, date_debut__lte=aujourd_hui,
                                           date_fin__gte=aujourd_hui)
          .prefetch_related('programmes', 'licences'))
    for a in qs:
        for p in a.programmes.all():
            for l in a.licences.all():
                if (p.pk, a.session_id, l.pk) in cles:
                    ouvertes.add((p.pk, a.session_id, l.pk))
    return ouvertes


def peut_modifier(profil, matiere, delibere=None, ouvertes=None, ecoulee=None, valide=None):
    """
    Scolarité : année en cours (délibérée ou non), avec motif et PDF pour toute modification ;
    année écoulée : seulement le chef de la scolarité ou un super utilisateur.
    Chef de département et directeur de programme : les notes de leurs programmes tant que le semestre
    n'est pas délibéré (année en cours) ; un semestre délibéré ou une année écoulée ne s'ouvre que pendant
    une autorisation du chef de la scolarité (programme, session, niveau, du … au …).
    Directeur de programme : plus rien une fois le semestre VALIDÉ par le chef de département (qui peut encore
    corriger, ou annuler sa validation). L'enseignant d'une matière (séances de l'emploi du temps) : comme le
    directeur de programme, pour cette matière seulement. `delibere`, `ouvertes`, `ecoulee`, `valide` : déjà calculés.
    """
    enseigne = matiere.pk in profil.matieres_enseignees_ids
    dans_perimetre = profil.est_scolarite or (profil.gere_matieres and matiere.filiere_id in profil.programme_ids)
    if not dans_perimetre and not enseigne:
        return False
    if ecoulee is None:
        ecoulee = session_ecoulee(matiere.session)
    if profil.est_scolarite:
        return profil.est_chef_de(matiere.filiere) if ecoulee else True
    if delibere is None:
        delibere = est_delibere(matiere)
    if ecoulee or delibere:
        if ouvertes is None:
            ouvertes = autorisations_actives([matiere])
        return (matiere.filiere_id, matiere.session_id, matiere.licence_id) in ouvertes
    if profil.role == 'programme' or not dans_perimetre:
        # directeur de programme et enseignant de la matière : fermé après la validation du chef de département
        return not (est_valide(matiere) if valide is None else valide)
    return True


def enregistrer_notes(changements, utilisateur, apres_deliberation=False, motif='', justificatif=None,
                      import_notes=None):
    """
    changements : [(note existante ou None, etudiant_id, matiere, valeurs)].
    Crée ou modifie les notes. Une CRÉATION garde son auteur, sa date et l'import d'où elle vient ;
    chaque MODIFICATION est tracée (avant / après, auteur, motif, justificatif enregistré une seule
    fois, import). `justificatif` : fichier envoyé, ou nom d'un fichier déjà rangé dans le stockage
    privé (PDF de l'enseignant d'un import). `apres_deliberation` : booléen, ou {matière: booléen}.
    Renvoie (créées, modifiées).
    """
    from django.utils import timezone
    from .models import Note, NoteHistorique, stockage_prive
    nom_justificatif = justificatif if isinstance(justificatif, str) else ''
    if justificatif and not isinstance(justificatif, str):
        from .import_notes import _nom_court
        nom_justificatif = stockage_prive().save(
            f"justificatifs/notes/{timezone.now():%Y}/{_nom_court(justificatif.name, 'justificatif.pdf')}",
            justificatif, max_length=200)
    auteur = utilisateur if getattr(utilisateur, 'pk', None) else None
    maintenant = timezone.now()
    codes = codes_reglement((etudiant_id, matiere) for _, etudiant_id, matiere, _ in changements)
    creees, modifiees, historiques = [], [], []
    for note, etudiant_id, matiere, valeurs in changements:
        code = codes.get((etudiant_id, matiere.pk), '')
        if note is None:
            note = Note(etudiant_id=etudiant_id, matiere=matiere, saisie_par=auteur, date_saisie=maintenant,
                        import_saisie=import_notes, reglement=code)
            appliquer_saisie(note, valeurs)
            creees.append(note)
            continue
        if saisie_de(note) == {c: valeurs.get(c) for c in CHAMPS_SAISIE}:
            continue                                         # rien n'a changé
        avant = _etat(note)
        note.reglement = code
        appliquer_saisie(note, valeurs)
        note.date_maj = maintenant                           # bulk_update ne remplit pas auto_now
        modifiees.append(note)
        delibere = (apres_deliberation.get(matiere.pk, False) if isinstance(apres_deliberation, dict)
                    else apres_deliberation)
        historiques.append(NoteHistorique(note=note, utilisateur=auteur, avant=avant, apres=_etat(note),
                                          apres_deliberation=delibere, motif=motif,
                                          justificatif=nom_justificatif, import_notes=import_notes))
    Note.objects.bulk_create(creees, batch_size=500)
    if modifiees:
        Note.objects.bulk_update(modifiees, ['controle', 'tp', 'examen', 'moyenne', 'moyenne_deposee', 'rattrapage',
                                             'moyenne_finale', 'par_session', 'date_maj', 'reglement'], batch_size=500)
        NoteHistorique.objects.bulk_create(historiques, batch_size=500)
    return len(creees), len(modifiees)


def arrondi(valeur):
    """
    Moyenne TRONQUÉE à 2 décimales (6,525 → 6,52 ; 6,999 → 6,99), jamais arrondie au-dessus.
    Le petit epsilon corrige seulement les erreurs de calcul en virgule flottante (7,2 = 7,19999…).
    """
    return None if valeur is None else math.floor(valeur * 100 + 1e-6) / 100


def lettre(note, code=''):
    """Note littérale des relevés : A (≥ 8), B, C, D (≥ 5), E ; arrêté 877 : A+ … D-, E."""
    if note is None:
        return ''
    lettres = regles(code)['lettres']
    for seuil, l in lettres:
        if note >= seuil:
            return l
    return lettres[-1][1]


def moyenne_des_notes(controle, tp, examen, code=''):
    """
    Moyenne d'une matière à partir des notes données (None si aucune).
    Règles générales : contrôle 25 % + TP 35 % + examen 40 % (sans TP : 40 % + 60 %).
    Arrêté 877 : CC 40 % + CT 60 %, avec CC = contrôle (moyenne du contrôle et du TP si les deux
    sont donnés) et CT = examen. Notes incomplètes : mêmes poids, ramenés aux seules notes données.
    """
    r = regles(code)
    if r.get('calcul') == 'cc_ct':
        cc = [v for v in (controle, tp) if v is not None]
        donnees = {k: v for k, v in (('cc', sum(cc) / len(cc) if cc else None), ('ct', examen)) if v is not None}
        poids = r['poids_cc_ct']
    else:
        donnees = {k: v for k, v in (('controle', controle), ('tp', tp), ('examen', examen)) if v is not None}
        poids = r['poids_sans_tp'] if set(donnees) == {'controle', 'examen'} else r['poids']
    if not donnees:
        return None
    return arrondi(sum(donnees[k] * poids[k] for k in donnees) / sum(poids[k] for k in donnees))


def note_retenue(moyenne, rattrapage, code=''):
    """
    (note finale, retenue au rattrapage ?) : le rattrapage remplace la moyenne s'il est meilleur ;
    arrêté 877 : le rattrapage retenu est plafonné à 5/10 (art. 49).
    """
    plafond = regles(code).get('plafond_rattrapage')
    rattrapage_retenu = rattrapage if rattrapage is None or plafond is None else min(rattrapage, plafond)
    par_session = rattrapage_retenu is not None and (moyenne is None or rattrapage_retenu > moyenne)
    return arrondi(rattrapage_retenu if par_session else moyenne), par_session


def est_validee(moyenne):
    return moyenne is not None and moyenne >= regles()['seuil_validation']


def mention(moyenne, code=''):
    if moyenne is None:
        return ''
    for seuil, libelle in regles(code)['mentions']:
        if moyenne >= seuil:
            return libelle
    return ''


def observation(note, definitif=False):
    """
    État d'une note sur les relevés. Règles générales : Validée, Validée en session, Non validée,
    Note manquante. Arrêté 877 : Validée, Validée au rattrapage, À rattraper (semestre non délibéré,
    pas de rattrapage), Non validée (rattrapage passé, pas encore délibéré), Dette (délibéré, art. 71).
    """
    if note is None:
        return 'Note manquante'
    r = regles(note.reglement)
    if est_validee(note.moyenne_finale):
        return r['libelle_session'] if note.par_session else 'Validée'
    if not r.get('dettes'):
        return 'Non validée'
    if note.moyenne_finale is None:
        return 'Note manquante'
    if definitif:
        return 'Dette'
    return 'Non validée' if note.rattrapage is not None else 'À rattraper'


def _agreger(matieres, notes, etudiant_id, code=''):
    """Moyenne pondérée par les crédits, crédits validés et notes manquantes d'un ensemble de matières."""
    total = points = valides = manquantes = 0
    for m in matieres:
        note = notes.get((etudiant_id, m.pk))
        total += m.credit
        if note is None:
            manquantes += 1                     # pas de note : compte 0
            continue
        points += note * m.credit
        if est_validee(note):
            valides += m.credit
    moyenne = arrondi(points / total) if total and manquantes < len(matieres) else None
    return {'moyenne': moyenne, 'credits_valides': valides, 'credits_total': total,
            'notes_manquantes': manquantes, 'mention': mention(moyenne, code)}


def _rangs(resultats):
    """Rang 1, 2, 2, 4… par groupe (même programme, session, semestre / licence / cycle)."""
    from .models import Resultat
    groupes = defaultdict(list)
    for r in resultats:
        if r.moyenne_generale is not None or r.moyenne is not None:
            cle = (r.niveau, r.filiere_id, r.session_id, r.semestre_id, r.licence_id if r.niveau != Resultat.CYCLE else None,
                   r.cycle if r.niveau == Resultat.CYCLE else None)
            groupes[cle].append(r)
    for membres in groupes.values():
        note = lambda r: r.moyenne_generale if r.moyenne_generale is not None else r.moyenne   # noqa: E731
        membres.sort(key=note, reverse=True)
        precedent, rang = None, 0
        for position, r in enumerate(membres, start=1):
            if note(r) != precedent:
                rang, precedent = position, note(r)
            r.rang = rang


def annees_retenues(annees):
    """[(annee de session, inscription, résultat)] → {licence: (inscription, résultat)} : la dernière année."""
    derniere = {}
    for _, insc, resultat in sorted(annees, key=lambda a: a[0]):
        derniere[insc.licence_id] = (insc, resultat)
    return derniere


def moyenne_generale(moyenne_cycle, note_soutenance):
    """Cycle × 0,87 + soutenance × 0,13 s'il y a une note de soutenance, sinon la moyenne du cycle."""
    if moyenne_cycle is None or note_soutenance is None:
        return moyenne_cycle
    r = regles()
    return arrondi(moyenne_cycle * r['poids_cycle'] + note_soutenance * r['poids_soutenance'])


def actualiser_reglements(etudiant_ids):
    """
    Remet chaque note des étudiants donnés sous le règlement de son inscription (cycle ou session
    corrigés, inscription créée après la note…) et refait alors sa moyenne et sa note finale.
    Renvoie le nombre de notes changées.
    """
    from .models import Matiere, Note
    ids = list({i for i in etudiant_ids if i})
    if not ids or not settings.GPED_NOTES.get('reglements'):
        return 0
    lignes = list(Note.objects.filter(etudiant_id__in=ids).values_list('pk', 'etudiant_id', 'matiere_id', 'reglement'))
    matieres = Matiere.objects.only('session_id', 'filiere_id', 'licence_id').in_bulk({l[2] for l in lignes})
    codes = codes_reglement((etu, matieres[mat]) for _, etu, mat, _ in lignes)
    a_changer = {pk: codes.get((etu, mat), '') for pk, etu, mat, code in lignes if codes.get((etu, mat), '') != code}
    if not a_changer:
        return 0
    notes = list(Note.objects.filter(pk__in=a_changer))
    for n in notes:
        n.reglement = a_changer[n.pk]
        n.calculer()
    Note.objects.bulk_update(notes, ['reglement', 'moyenne', 'moyenne_finale', 'par_session'], batch_size=500)
    return len(notes)


def recalculer(etudiant_ids):
    """
    Recalcule les résultats (semestres, années, cycles) des étudiants donnés, puis les rangs de
    leurs groupes (programme, session…). Rapide : quelques requêtes quel que soit le volume.
    """
    from .models import Inscription, Matiere, Note, Resultat, Soutenance
    ids = list({i for i in etudiant_ids if i})
    if not ids:
        return
    # Année en congé académique : suspendue, elle ne compte pas (ni moyenne, ni rang, ni cycle)
    inscriptions = list(Inscription.objects.filter(etudiant_id__in=ids, filiere__isnull=False, conge__isnull=True)
                        .select_related('session'))
    triplets = {(i.filiere_id, i.session_id, i.licence_id) for i in inscriptions}
    matieres_par_triplet = defaultdict(list)
    if triplets:
        for m in Matiere.objects.filter(filiere_id__in={t[0] for t in triplets},
                                        session_id__in={t[1] for t in triplets},
                                        licence_id__in={t[2] for t in triplets}):
            if (m.filiere_id, m.session_id, m.licence_id) in triplets:
                matieres_par_triplet[(m.filiere_id, m.session_id, m.licence_id)].append(m)
    actualiser_reglements(ids)
    notes = {(etu, mat): moy for etu, mat, moy in
             Note.objects.filter(etudiant_id__in=ids).values_list('etudiant_id', 'matiere_id', 'moyenne_finale')}

    nouveaux, annees_par_programme = [], defaultdict(list)
    debuts = debuts_cycle(ids)
    for insc in inscriptions:
        matieres = matieres_par_triplet.get((insc.filiere_id, insc.session_id, insc.licence_id))
        if not matieres:
            continue
        code = code_inscription(insc, debuts)
        commun = {'etudiant_id': insc.etudiant_id, 'filiere_id': insc.filiere_id, 'session_id': insc.session_id,
                  'licence_id': insc.licence_id, 'cycle': insc.cycle}
        par_semestre = defaultdict(list)
        for m in matieres:
            par_semestre[m.semestre_id].append(m)
        for semestre_id, ms in par_semestre.items():
            nouveaux.append(Resultat(niveau=Resultat.SEMESTRE, semestre_id=semestre_id, **commun,
                                     **_agreger(ms, notes, insc.etudiant_id, code)))
        annee = _agreger(matieres, notes, insc.etudiant_id, code)
        nouveaux.append(Resultat(niveau=Resultat.ANNEE, **commun, **annee))
        annees_par_programme[(insc.etudiant_id, insc.filiere_id)].append((insc.session.annee, insc, annee))

    # Cycle, par programme : une année par licence (la plus récente en cas de redoublement) + soutenance
    soutenances = {(etu, prog): note for etu, prog, note in Soutenance.objects.filter(etudiant_id__in=ids)
                   .values_list('etudiant_id', 'filiere_id', 'note_finale')}
    for (etudiant_id, filiere_id), annees in annees_par_programme.items():
        retenues = list(annees_retenues(annees).values())
        derniere_insc = max((i for i, _ in retenues), key=lambda i: i.session.annee)
        code = code_inscription(derniere_insc, debuts)
        note_soutenance = soutenances.get((etudiant_id, filiere_id))
        if regles(code)['moyenne_cycle'] == 'cumulative':
            # Arrêté 877 : moyenne générale cumulative de toutes les matières du programme, pondérée par les
            # crédits (le PFE y entrera comme une matière de S10 ; la soutenance n'est pas ajoutée à part)
            ms = [m for i, _ in retenues for m in matieres_par_triplet[(i.filiere_id, i.session_id, i.licence_id)]]
            moyenne_cycle = generale = _agreger(ms, notes, etudiant_id, code)['moyenne']
        else:
            moyennes = [a['moyenne'] for _, a in retenues if a['moyenne'] is not None]
            moyenne_cycle = arrondi(sum(moyennes) / len(moyennes)) if moyennes else None
            generale = moyenne_generale(moyenne_cycle, note_soutenance)
        nouveaux.append(Resultat(
            niveau=Resultat.CYCLE, etudiant_id=etudiant_id, cycle=derniere_insc.cycle, filiere_id=filiere_id,
            session_id=derniere_insc.session_id, moyenne=moyenne_cycle, soutenance=note_soutenance,
            moyenne_generale=generale, mention=mention(generale, code),
            credits_valides=sum(a['credits_valides'] for _, a in retenues),
            credits_total=sum(a['credits_total'] for _, a in retenues),
            notes_manquantes=sum(a['notes_manquantes'] for _, a in retenues)))

    with transaction.atomic():
        anciens = Resultat.objects.filter(etudiant_id__in=ids)
        # Groupes d'où un résultat disparaît (congé académique) : leurs rangs sont refaits aussi
        groupes = set(anciens.values_list('niveau', 'filiere_id', 'session_id'))
        anciens.delete()
        Resultat.objects.bulk_create(nouveaux, batch_size=500)
        # Rangs : on reprend tous les étudiants des groupes touchés (pas seulement ceux recalculés)
        groupes |= {(x.niveau, x.filiere_id, x.session_id) for x in nouveaux}
        concernes = []
        for niveau, filiere_id, session_id in groupes:
            concernes.extend(Resultat.objects.filter(niveau=niveau, filiere_id=filiere_id, session_id=session_id))
        _rangs(concernes)
        Resultat.objects.bulk_update(concernes, ['rang'], batch_size=500)
