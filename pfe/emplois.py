"""
Emplois du temps : règles et conflits (voir le modèle Seance).

- Une semaine type par semestre. Les semestres impairs (1, 3, 5…) ont lieu en même temps, de même que les pairs :
  un enseignant ou une salle n'est en conflit qu'avec les cours de la même session et de la même parité.
- Conflits : deux cours de la même classe au même moment, un enseignant déjà pris, une salle déjà prise, une salle
  trop petite pour l'effectif de la classe. Ils s'affichent en rouge clair ; l'utilisateur peut forcer.
"""
import re

from .models import Inscription, Seance


def periode(semestre):
    """1 : semestres impairs ; 0 : pairs ; None : inconnu (en conflit avec tous)."""
    m = re.search(r'\d+', (semestre.nom if semestre else '') or '')
    return int(m.group()) % 2 if m else None


def meme_periode(s1, s2):
    p1, p2 = periode(s1), periode(s2)
    return p1 is None or p2 is None or p1 == p2


def cle_classe(m):
    return (m.filiere_id, m.licence_id, m.semestre_id, m.session_id)


def libelle_classe(m):
    return f"{m.filiere.nom if m.filiere else '—'} · {m.licence.nom} · {m.semestre.nom}"


def effectif(matiere):
    """Inscrits de la classe (programme, niveau, session), hors préinscrits et congés."""
    return Inscription.objects.filter(filiere_id=matiere.filiere_id, licence_id=matiere.licence_id,
                                      session_id=matiere.session_id, etudiant__preinscrit=False,
                                      conge__isnull=True).count()


def candidates(session_id, jours=None):
    qs = (Seance.objects.filter(matiere__session_id=session_id)
          .select_related('matiere__filiere', 'matiere__licence', 'matiere__semestre', 'salle__batiment')
          .prefetch_related('enseignants'))
    if jours:
        qs = qs.filter(jour__in=jours)
    return list(qs)


def conflits(matiere, jour, debut, fin, salle, enseignant_ids, autres, exclure=None, nb_inscrits=None):
    """Messages de conflit d'un cours (projeté ou enregistré) face aux `autres` cours (mêmes session)."""
    messages, enseignant_ids = [], set(enseignant_ids)
    for o in autres:
        if o.pk == exclure or o.jour != jour or not (debut < o.fin and o.debut < fin):
            continue
        horaire = f"{o.get_jour_display()} {Seance.heure(o.debut)}–{Seance.heure(o.fin)}"
        if cle_classe(o.matiere) == cle_classe(matiere):
            messages.append(('classe', f"La classe a déjà « {o.matiere.nom} » ({horaire})."))
            continue
        if not meme_periode(o.matiere.semestre, matiere.semestre):
            continue
        communs = [e for e in o.enseignants.all() if e.pk in enseignant_ids]
        for e in communs:
            messages.append(('enseignant', f"{e.prenom} {e.nom} est déjà pris : « {o.matiere.nom} », "
                                           f"{libelle_classe(o.matiere)} ({horaire})."))
        if salle is not None and o.salle_id == salle.pk:
            messages.append(('salle', f"La salle {salle.nom} est déjà prise : « {o.matiere.nom} », "
                                      f"{libelle_classe(o.matiere)} ({horaire})."))
    if salle is not None:
        n = effectif(matiere) if nb_inscrits is None else nb_inscrits
        if n > salle.capacite:
            messages.append(('capacite', f"La salle {salle.nom} a {salle.capacite} places pour {n} inscrits."))
    return messages


def en_json(s, messages=()):
    return {
        'id': s.pk, 'matiere': s.matiere_id, 'nom': s.matiere.nom, 'code': s.matiere.code or '',
        'classe': libelle_classe(s.matiere), 'type': s.type, 'type_court': Seance.COURTS.get(s.type, s.type), 'jour': s.jour, 'debut': s.debut, 'fin': s.fin,
        'salle': ({'id': s.salle_id, 'nom': s.salle.nom, 'batiment': s.salle.batiment.code or s.salle.batiment.nom,
                   'capacite': s.salle.capacite} if s.salle_id else None),
        'enseignants': [{'id': e.pk, 'nom': f"{e.civilite + ' ' if e.civilite else ''}{e.prenom} {e.nom}"}
                        for e in s.enseignants.all()],
        'conflits': [m for _, m in messages], 'force': s.conflit_force,
    }


def avec_conflits(seances, session_id, effectifs=None):
    """[(séance, messages)] : chaque séance face à tous les cours de la session (effectifs mis en cache)."""
    autres = candidates(session_id, {s.jour for s in seances})
    effectifs = {} if effectifs is None else effectifs
    resultat = []
    for s in seances:
        cle = cle_classe(s.matiere)
        if s.salle_id and cle not in effectifs:
            effectifs[cle] = effectif(s.matiere)
        resultat.append((s, conflits(s.matiere, s.jour, s.debut, s.fin, s.salle, [e.pk for e in s.enseignants.all()],
                                     autres, exclure=s.pk, nb_inscrits=effectifs.get(cle))))
    return resultat


def creneaux():
    """[(minutes, « 07h00 »)] de 7 h à 22 h par pas de 30 minutes (début de chaque tranche)."""
    return [(m, Seance.heure(m)) for m in range(Seance.DEBUT_JOURNEE, Seance.FIN_JOURNEE, Seance.PAS)]
