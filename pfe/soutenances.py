"""
Soutenances : périmètre, enregistrement des notes (avec historique), verrouillage, calendrier.

Règles :
  - note sur 10, absent = 0, validée à partir de 5 ; une note de session (soutenance repassée)
    garde la meilleure des deux, avec la mention « Session » ;
  - la note retenue entre dans la moyenne générale du cycle (voir pfe/notes.py) ;
  - verrouillage : bouton « Verrouiller les notes » après la saisie, ou d'office à l'import.
    Une note verrouillée ne se modifie plus que par la scolarité, avec un motif (et un justificatif).
    Les informations du projet de soutenance (thème, encadreur, jury…) restent modifiables ;
  - droits : la scolarité gère tout, un chef de programme ses programmes.
"""
import unicodedata
from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from .models import ProjetSoutenance, Soutenance, SoutenanceHistorique, stockage_prive

CHAMPS_NOTE = ('note', 'note_session')
# Cycles où l'on soutient : un autre cycle est accepté, avec un simple avertissement
CYCLES_SOUTENANCE = ('Ingénieur', 'Master')


def avertissement_cycle(cycle):
    if cycle and cycle not in CYCLES_SOUTENANCE:
        return f"Attention : la soutenance concerne d'habitude les cycles Ingénieur et Master (ici : {cycle})."
    return ''


def soutenances_visibles(profil):
    qs = Soutenance.objects.select_related('etudiant', 'filiere', 'session', 'promotion', 'projet')
    return qs if profil.est_scolarite else qs.filter(filiere_id__in=profil.programme_ids)


def projets_visibles(profil):
    qs = ProjetSoutenance.objects.select_related('filiere', 'session', 'promotion')
    return qs if profil.est_scolarite else qs.filter(filiere_id__in=profil.programme_ids)


def peut_modifier_note(profil, soutenance):
    """Note verrouillée : la scolarité seulement (avec motif)."""
    return profil.est_scolarite or not soutenance.verrouillee


def notes_de(s):
    return {c: getattr(s, c) for c in CHAMPS_NOTE}


def _etat(s):
    return {**notes_de(s), 'note_finale': s.note_finale, 'session': s.par_session}


def enregistrer_notes(changements, utilisateur, motif='', justificatif=None):
    """
    changements : [(soutenance, {'note': …, 'note_session': …})]. Chaque modification d'une note
    déjà saisie est tracée (avant / après, auteur, motif, justificatif enregistré une seule fois).
    Renvoie le nombre de soutenances modifiées.
    """
    nom_justificatif = ''
    if justificatif:
        nom_justificatif = stockage_prive().save(
            f"justificatifs/soutenances/{timezone.now():%Y}/{justificatif.name}", justificatif)
    modifiees, historiques = [], []
    for s, valeurs in changements:
        if notes_de(s) == {c: valeurs.get(c) for c in CHAMPS_NOTE}:
            continue
        avant = _etat(s)
        for c in CHAMPS_NOTE:
            setattr(s, c, valeurs.get(c))
        s.calculer()
        s.date_maj = timezone.now()                      # bulk_update ne remplit pas auto_now
        modifiees.append(s)
        if any(avant[c] is not None for c in CHAMPS_NOTE):
            historiques.append(SoutenanceHistorique(soutenance=s, utilisateur=utilisateur, avant=avant,
                                                    apres=_etat(s), note_verrouillee=s.verrouillee,
                                                    motif=motif, justificatif=nom_justificatif))
    if modifiees:
        Soutenance.objects.bulk_update(modifiees, ['note', 'note_session', 'note_finale', 'par_session', 'date_maj'],
                                       batch_size=500)
        SoutenanceHistorique.objects.bulk_create(historiques, batch_size=500)
    return len(modifiees)


def verrouiller(soutenances, utilisateur):
    """Verrouille les notes saisies (celles sans note restent ouvertes). Renvoie le nombre verrouillé."""
    ids = [s.pk for s in soutenances if s.note_finale is not None and not s.verrouillee]
    if ids:
        Soutenance.objects.filter(pk__in=ids).update(verrouillee=True, verrouillee_le=timezone.now(),
                                                     verrouillee_par=utilisateur)
    return len(ids)


def deverrouiller(soutenances):
    ids = [s.pk for s in soutenances if s.verrouillee]
    if ids:
        Soutenance.objects.filter(pk__in=ids).update(verrouillee=False, verrouillee_le=None, verrouillee_par=None)
    return len(ids)


def filtrer(qs, f, prefixe=''):
    """Filtres communs (session, programme, promotion, recherche) des soutenances ou des projets."""
    p = prefixe
    if f.get('session'):
        qs = qs.filter(**{f'{p}session_id': f['session']})
    if f.get('programme'):
        qs = qs.filter(**{f'{p}filiere_id': f['programme']})
    if f.get('promotion'):
        qs = qs.filter(**{f'{p}promotion_id': f['promotion']})
    return qs


def recherche_soutenances(qs, q):
    if not q:
        return qs
    return qs.filter(Q(etudiant__matricule__icontains=q) | Q(etudiant__nom__icontains=q)
                     | Q(etudiant__prenom__icontains=q) | Q(projet__intitule__icontains=q)
                     | Q(projet__theme__icontains=q) | Q(projet__encadreur__icontains=q))


def recherche_projets(qs, q):
    if not q:
        return qs
    return qs.filter(Q(intitule__icontains=q) | Q(theme__icontains=q) | Q(encadreur__icontains=q)
                     | Q(membres__etudiant__matricule__icontains=q) | Q(membres__etudiant__nom__icontains=q)).distinct()


# ---------------------------------------------------------------- calendrier

def _cle_nom(texte):
    s = unicodedata.normalize('NFKD', str(texte or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().replace('.', ' ').split())


def conflits(projets):
    """
    {projet_id: [messages]} : même salle, ou même membre du jury, au même créneau (date + heure).
    Les projets sans date ou sans heure ne sont pas comparés.
    """
    creneaux = defaultdict(list)
    for p in projets:
        if p.date and p.heure:
            creneaux[(p.date, p.heure)].append(p)
    resultat = defaultdict(list)
    for (date, heure), liste in creneaux.items():
        if len(liste) < 2:
            continue
        salles, jures = defaultdict(list), defaultdict(list)
        for p in liste:
            if p.salle.strip():
                salles[_cle_nom(p.salle)].append(p)
            for nom in {_cle_nom(n) for n in (p.president_jury, p.examinateur, p.rapporteur) if n.strip()}:
                jures[nom].append(p)
        for salle, ps in salles.items():
            if len(ps) > 1:
                for p in ps:
                    autres = ', '.join(x.intitule for x in ps if x.pk != p.pk)
                    resultat[p.pk].append(f"Salle {p.salle} déjà prise à {heure:%H:%M} par : {autres}")
        for nom, ps in jures.items():
            if len(ps) > 1:
                for p in ps:
                    autres = ', '.join(x.intitule for x in ps if x.pk != p.pk)
                    resultat[p.pk].append(f"Jury : {nom.title()} est aussi attendu à {heure:%H:%M} pour : {autres}")
    return dict(resultat)
