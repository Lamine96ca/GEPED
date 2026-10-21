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
Cycle    : moyenne des années du cycle (pour une licence redoublée : la dernière année) ;
           moyenne générale = cycle × 0,87 + soutenance × 0,13 (note retenue : la meilleure
           de la note de soutenance et de la note de session, voir pfe/soutenances.py).
Mentions : 5 Passable, 6 Assez bien, 7 Très bien, 8 et plus Excellent.

Les résultats sont enregistrés dans Resultat par recalculer(), à appeler après une saisie,
un import ou une soutenance (pour les seuls étudiants concernés).
"""
from collections import defaultdict

from django.conf import settings
from django.db import transaction


CHAMPS_SAISIE = ('controle', 'tp', 'examen', 'moyenne', 'rattrapage')


def regles():
    return settings.GPED_NOTES


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
    return round(v, 2)


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


def peut_modifier(profil, matiere, delibere):
    """Avant délibération : scolarité et chef du programme ; après : la scolarité seulement (avec motif)."""
    if delibere:
        return profil.est_scolarite
    return profil.est_scolarite or (profil.role == 'programme' and matiere.filiere_id in profil.programme_ids)


def enregistrer_notes(changements, utilisateur, apres_deliberation=False, motif='', justificatif=None):
    """
    changements : [(note existante ou None, etudiant_id, matiere, valeurs)].
    Crée ou modifie les notes ; chaque MODIFICATION est tracée (avant / après, auteur, motif,
    justificatif enregistré une seule fois). Renvoie (créées, modifiées).
    """
    from .models import Note, NoteHistorique, stockage_prive
    nom_justificatif = ''
    if justificatif:
        from django.utils import timezone
        nom_justificatif = stockage_prive().save(f"justificatifs/notes/{timezone.now():%Y}/{justificatif.name}",
                                                 justificatif)
    creees, modifiees, historiques = [], [], []
    for note, etudiant_id, matiere, valeurs in changements:
        if note is None:
            note = Note(etudiant_id=etudiant_id, matiere=matiere)
            appliquer_saisie(note, valeurs)
            creees.append(note)
            continue
        if saisie_de(note) == {c: valeurs.get(c) for c in CHAMPS_SAISIE}:
            continue                                         # rien n'a changé
        avant = _etat(note)
        appliquer_saisie(note, valeurs)
        from django.utils import timezone
        note.date_maj = timezone.now()                       # bulk_update ne remplit pas auto_now
        modifiees.append(note)
        historiques.append(NoteHistorique(note=note, utilisateur=utilisateur, avant=avant, apres=_etat(note),
                                          apres_deliberation=apres_deliberation, motif=motif,
                                          justificatif=nom_justificatif))
    Note.objects.bulk_create(creees, batch_size=500)
    if modifiees:
        Note.objects.bulk_update(modifiees, ['controle', 'tp', 'examen', 'moyenne', 'moyenne_deposee', 'rattrapage',
                                             'moyenne_finale', 'par_session', 'date_maj'], batch_size=500)
        NoteHistorique.objects.bulk_create(historiques, batch_size=500)
    return len(creees), len(modifiees)


def arrondi(valeur):
    return None if valeur is None else round(valeur + 1e-9, 2)


def moyenne_des_notes(controle, tp, examen):
    """Moyenne d'une matière à partir des notes données (None si aucune)."""
    r = regles()
    donnees = {k: v for k, v in (('controle', controle), ('tp', tp), ('examen', examen)) if v is not None}
    if not donnees:
        return None
    poids = r['poids_sans_tp'] if set(donnees) == {'controle', 'examen'} else {k: r['poids'][k] for k in donnees}
    return arrondi(sum(donnees[k] * poids[k] for k in donnees) / sum(poids.values()))


def est_validee(moyenne):
    return moyenne is not None and moyenne >= regles()['seuil_validation']


def mention(moyenne):
    if moyenne is None:
        return ''
    for seuil, libelle in regles()['mentions']:
        if moyenne >= seuil:
            return libelle
    return ''


def _agreger(matieres, notes, etudiant_id):
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
            'notes_manquantes': manquantes, 'mention': mention(moyenne)}


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


def recalculer(etudiant_ids):
    """
    Recalcule les résultats (semestres, années, cycles) des étudiants donnés, puis les rangs de
    leurs groupes (programme, session…). Rapide : quelques requêtes quel que soit le volume.
    """
    from .models import Inscription, Matiere, Note, Resultat, Soutenance
    ids = list({i for i in etudiant_ids if i})
    if not ids:
        return
    r = regles()
    inscriptions = list(Inscription.objects.filter(etudiant_id__in=ids, filiere__isnull=False)
                        .select_related('session'))
    triplets = {(i.filiere_id, i.session_id, i.licence_id) for i in inscriptions}
    matieres_par_triplet = defaultdict(list)
    if triplets:
        for m in Matiere.objects.filter(filiere_id__in={t[0] for t in triplets},
                                        session_id__in={t[1] for t in triplets},
                                        licence_id__in={t[2] for t in triplets}):
            if (m.filiere_id, m.session_id, m.licence_id) in triplets:
                matieres_par_triplet[(m.filiere_id, m.session_id, m.licence_id)].append(m)
    notes = {(etu, mat): moy for etu, mat, moy in
             Note.objects.filter(etudiant_id__in=ids).values_list('etudiant_id', 'matiere_id', 'moyenne_finale')}

    nouveaux, annees_par_cycle = [], defaultdict(list)
    for insc in inscriptions:
        matieres = matieres_par_triplet.get((insc.filiere_id, insc.session_id, insc.licence_id))
        if not matieres:
            continue
        commun = {'etudiant_id': insc.etudiant_id, 'filiere_id': insc.filiere_id, 'session_id': insc.session_id,
                  'licence_id': insc.licence_id, 'cycle': insc.cycle}
        par_semestre = defaultdict(list)
        for m in matieres:
            par_semestre[m.semestre_id].append(m)
        for semestre_id, ms in par_semestre.items():
            nouveaux.append(Resultat(niveau=Resultat.SEMESTRE, semestre_id=semestre_id, **commun,
                                     **_agreger(ms, notes, insc.etudiant_id)))
        annee = _agreger(matieres, notes, insc.etudiant_id)
        nouveaux.append(Resultat(niveau=Resultat.ANNEE, **commun, **annee))
        if insc.cycle:
            annees_par_cycle[(insc.etudiant_id, insc.cycle)].append((insc.session.annee, insc, annee))

    # Cycle : une année par licence (la plus récente en cas de redoublement) + soutenance
    soutenances = {(etu, cycle): note for etu, cycle, note in Soutenance.objects.filter(etudiant_id__in=ids)
                   .values_list('etudiant_id', 'cycle', 'note_finale')}
    for (etudiant_id, cycle), annees in annees_par_cycle.items():
        derniere = {}
        for _, insc, annee in sorted(annees, key=lambda a: a[0]):
            derniere[insc.licence_id] = (insc, annee)
        retenues = list(derniere.values())
        moyennes = [a['moyenne'] for _, a in retenues if a['moyenne'] is not None]
        moyenne_cycle = arrondi(sum(moyennes) / len(moyennes)) if moyennes else None
        note_soutenance = soutenances.get((etudiant_id, cycle))
        generale = (arrondi(moyenne_cycle * r['poids_cycle'] + note_soutenance * r['poids_soutenance'])
                    if moyenne_cycle is not None and note_soutenance is not None else None)
        derniere_insc = max((i for i, _ in retenues), key=lambda i: i.session.annee)
        nouveaux.append(Resultat(
            niveau=Resultat.CYCLE, etudiant_id=etudiant_id, cycle=cycle, filiere_id=derniere_insc.filiere_id,
            session_id=derniere_insc.session_id, moyenne=moyenne_cycle, soutenance=note_soutenance,
            moyenne_generale=generale, mention=mention(generale if generale is not None else moyenne_cycle),
            credits_valides=sum(a['credits_valides'] for _, a in retenues),
            credits_total=sum(a['credits_total'] for _, a in retenues),
            notes_manquantes=sum(a['notes_manquantes'] for _, a in retenues)))

    with transaction.atomic():
        Resultat.objects.filter(etudiant_id__in=ids).delete()
        Resultat.objects.bulk_create(nouveaux, batch_size=500)
        # Rangs : on reprend tous les étudiants des groupes touchés (pas seulement ceux recalculés)
        groupes = {(x.niveau, x.filiere_id, x.session_id) for x in nouveaux}
        concernes = []
        for niveau, filiere_id, session_id in groupes:
            concernes.extend(Resultat.objects.filter(niveau=niveau, filiere_id=filiere_id, session_id=session_id))
        _rangs(concernes)
        Resultat.objects.bulk_update(concernes, ['rang'], batch_size=500)
