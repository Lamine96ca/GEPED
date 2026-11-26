"""
Heures des enseignants : prévues (emploi du temps), faites (cours validés par un délégué), équivalent TD, service
statutaire et heures supplémentaires. Tous les nombres viennent des paramètres de la CIAQ (ParametresHeures) :
rien n'est figé dans le code.

- Prévu : heures par semaine de l'emploi du temps × semaines (du semestre, ou écoulées à ce jour d'après les dates
  des semestres saisies par la Direction des Études).
- Fait : somme des durées (départ − arrivée, données par le délégué) des cours VALIDÉS ; un contrôle compte comme une
  heure de cours. En équivalent TD si la CIAQ l'active (coefficients par type).
- Permanents : service du semestre selon le grade (réduit pour une fonction administrative) ; heures supplémentaires
  au-delà du service, plafonnées (par semaine × semaines, et en % du service), payées au taux du grade.
- Vacataires : pas de service ni de plafond ; leurs heures sont celles de l'emploi du temps (comparées aux faites).
"""
import math
from collections import defaultdict
from datetime import date as _date

from . import emplois
from .models import DatesSemestre, Enseignant, ParametresHeures, Seance, SeanceCours

PERIODES = [('impair', 'Semestres impairs (1, 3, 5…)'), ('pair', 'Semestres pairs (2, 4, 6…)')]
RANG_A = ('professeur', 'maitre_conferences')


def parite(code):
    return 1 if code == 'impair' else 0


def dans_periode(matiere, code):
    return emplois.periode(matiere.semestre) in (parite(code), None)


def semaines_ecoulees(session, code, params=None, aujourd_hui=None):
    """Semaines écoulées du semestre (dates saisies par la Direction des Études) ; None si les dates manquent."""
    params = params or ParametresHeures.actuels()
    d = DatesSemestre.objects.filter(session=session, periode=code).first() if session else None
    if d is None:
        return None
    jour = aujourd_hui or _date.today()
    if jour < d.debut:
        return 0
    return min(params.semaines_semestre, math.ceil(((min(jour, d.fin) - d.debut).days + 1) / 7))


def fmt(h):
    """4.5 → « 4 h 30 » ; None → « — »."""
    if h is None:
        return '—'
    minutes = round(h * 60)
    signe = '-' if minutes < 0 else ''
    minutes = abs(minutes)
    return f"{signe}{minutes // 60} h {minutes % 60:02d}" if minutes % 60 else f"{signe}{minutes // 60} h"


def gnf(montant):
    return f"{round(montant):,}".replace(',', ' ') + ' GNF' if montant is not None else '—'


# ------------------------------------------------------------------ prévu (emploi du temps)

def hebdo_edt(session, code, enseignants=None, matieres=None):
    """{(enseignant_id, matiere_id): {type: heures par semaine}} d'après l'emploi du temps de la période."""
    qs = Seance.objects.filter(matiere__session=session).select_related('matiere__semestre').prefetch_related(
        'enseignants')
    if matieres is not None:
        qs = qs.filter(matiere__in=matieres)
    ids = {e.pk for e in enseignants} if enseignants is not None else None
    res = defaultdict(lambda: defaultdict(float))
    for s in qs:
        if not dans_periode(s.matiere, code):
            continue
        for e in s.enseignants.all():
            if ids is None or e.pk in ids:
                res[(e.pk, s.matiere_id)][s.type] += (s.fin - s.debut) / 60
    return res


# ------------------------------------------------------------------ fait (cours validés)

def seances_validees(session, code, enseignants=None, matieres=None):
    qs = (SeanceCours.objects.filter(matiere__session=session, statut=SeanceCours.VALIDEE)
          .select_related('matiere__semestre', 'matiere__filiere', 'matiere__licence', 'enseignant', 'validee_par'))
    if enseignants is not None:
        qs = qs.filter(enseignant__in=enseignants)
    if matieres is not None:
        qs = qs.filter(matiere__in=matieres)
    return [s for s in qs if dans_periode(s.matiere, code)]


def comparaison(session, code, enseignants=None, matieres=None, params=None):
    """
    Par (enseignant, matière) : heures prévues par semaine, prévues sur le semestre et à ce jour, faites (validées),
    en attente ; pour les graphiques « prévu / fait ».
    """
    params = params or ParametresHeures.actuels()
    semaines = semaines_ecoulees(session, code, params)
    prevu = hebdo_edt(session, code, enseignants, matieres)
    faites = defaultdict(float)
    for s in seances_validees(session, code, enseignants, matieres):
        faites[(s.enseignant_id, s.matiere_id)] += s.duree or 0
    attente = defaultdict(int)
    qs = SeanceCours.objects.filter(matiere__session=session, statut__in=SeanceCours.EN_COURS)
    if enseignants is not None:
        qs = qs.filter(enseignant__in=enseignants)
    for e_id, m_id in qs.values_list('enseignant_id', 'matiere_id'):
        attente[(e_id, m_id)] += 1
    lignes = []
    for cle in set(prevu) | set(faites):
        hebdo = sum(prevu.get(cle, {}).values())
        lignes.append({'enseignant_id': cle[0], 'matiere_id': cle[1], 'hebdo': hebdo,
                       'semestre': hebdo * params.semaines_semestre,
                       'a_ce_jour': hebdo * semaines if semaines is not None else None,
                       'fait': faites.get(cle, 0), 'attente': attente.get(cle, 0)})
    return lignes, semaines


def avec_barres(lignes):
    """Ajoute à chaque ligne les largeurs (%) des barres prévu / fait, à l'échelle de la plus grande valeur."""
    maximum = max([max(l['a_ce_jour'] if l['a_ce_jour'] is not None else l['semestre'], l['fait']) for l in lignes]
                  or [0]) or 1
    for l in lignes:
        reference = l['a_ce_jour'] if l['a_ce_jour'] is not None else l['semestre']
        l['reference'] = reference
        l['barre_prevu'] = round(100 * reference / maximum, 1)
        l['barre_fait'] = round(100 * l['fait'] / maximum, 1)
        l['ecart'] = l['fait'] - reference
        l['pct'] = round(100 * l['fait'] / reference) if reference else None
        for k in ('hebdo', 'semestre', 'a_ce_jour', 'fait', 'reference', 'ecart'):
            l[k + '_txt'] = fmt(l[k])
    return lignes


# ------------------------------------------------------------------ situation des enseignants

def situation(session, code, enseignants=None, params=None):
    """
    (permanents, vacataires) pour la session et la période : une ligne par enseignant (actif) qui a des séances dans
    l'emploi du temps ou des cours validés.
    """
    params = params or ParametresHeures.actuels()
    qs = Enseignant.objects.filter(actif=True).select_related('departement')
    if enseignants is not None:
        qs = qs.filter(pk__in=[e.pk for e in enseignants])
    ens = {e.pk: e for e in qs}
    prevu = hebdo_edt(session, code, list(ens.values()))
    faits = defaultdict(lambda: defaultdict(float))
    for s in seances_validees(session, code, list(ens.values())):
        faits[s.enseignant_id][s.type] += s.duree or 0
    hebdo_par_ens = defaultdict(lambda: defaultdict(float))
    for (e_id, _), types in prevu.items():
        for t, h in types.items():
            hebdo_par_ens[e_id][t] += h
    permanents, vacataires = [], []
    for e_id in set(hebdo_par_ens) | set(faits):
        e = ens.get(e_id)
        if e is None:
            continue
        fait_types = faits.get(e_id, {})
        fait = sum(fait_types.values())
        eq_td = sum(h * params.coefficient(t) for t, h in fait_types.items())
        hebdo = sum(hebdo_par_ens.get(e_id, {}).values())
        g = params.grade(e.grade)
        ligne = {'enseignant': e, 'grade': e.get_grade_display() or '—', 'hebdo': hebdo,
                 'edt_semestre': hebdo * params.semaines_semestre, 'fait': fait, 'eq_td': eq_td,
                 'par_type': {SeanceCours.COURTS.get(t, t): fmt(h) for t, h in sorted(fait_types.items())},
                 'taux': g.get('taux'), 'alertes': []}
        if e.statut == Enseignant.VACATAIRE:
            ligne['montant'] = ligne['edt_semestre'] * g['taux'] if g.get('taux') else None
            if not g.get('taux'):
                ligne['alertes'].append("taux horaire non défini pour ce grade")
            vacataires.append(ligne)
            continue
        service = g.get('service')
        if service is None:
            ligne.update({'service': None, 'hs': None, 'montant': None})
            ligne['alertes'].append("service non défini pour ce grade (paramètres)")
        else:
            service = float(service) * (1 - (params.reduction_fonction / 100 if e.fonction_administrative else 0))
            brut = max(0.0, eq_td - service)
            plafonds = []
            if params.hs_plafond_pct is not None:
                plafonds.append(service * params.hs_plafond_pct / 100)
            if params.hs_plafond_hebdo is not None:
                plafonds.append(float(params.hs_plafond_hebdo) * params.semaines_semestre)
            hs = min([brut] + plafonds)
            if hs < brut:
                ligne['alertes'].append(f"heures supplémentaires plafonnées ({fmt(brut)} au-delà du service)")
            ligne.update({'service': service, 'hs': hs, 'hs_brut': brut,
                          'reste': max(0.0, service - eq_td),
                          'montant': hs * g['taux'] if g.get('taux') else None})
            if not g.get('taux') and hs:
                ligne['alertes'].append("taux horaire non défini pour ce grade")
        if e.grade in RANG_A and params.cm_rang_a_pct and eq_td:
            part_cm = fait_types.get(SeanceCours.CM, 0) * params.coefficient(SeanceCours.CM) / eq_td * 100
            if part_cm < params.cm_rang_a_pct:
                ligne['alertes'].append(f"rang A : {round(part_cm)} % de CM (au moins {params.cm_rang_a_pct} %)")
        if params.plafonds_par_type:
            for t, plafond in ((SeanceCours.CM, params.plafond_cm), (SeanceCours.TD, params.plafond_td),
                               (SeanceCours.TP, params.plafond_tp)):
                if plafond is not None and fait_types.get(t, 0) > float(plafond):
                    ligne['alertes'].append(f"{SeanceCours.COURTS[t]} : {fmt(fait_types[t])} pour {fmt(float(plafond))} "
                                            f"prévues")
        permanents.append(ligne)
    for l in permanents + vacataires:
        for k in ('hebdo', 'edt_semestre', 'fait', 'eq_td', 'service', 'hs', 'reste'):
            if k in l:
                l[k + '_txt'] = fmt(l[k])
        l['montant_txt'] = gnf(l.get('montant'))
        l['taux_txt'] = gnf(l.get('taux')) if l.get('taux') else '—'
    cle = lambda l: (l['enseignant'].departement.nom if l['enseignant'].departement_id else '', l['enseignant'].nom)  # noqa: E731
    return sorted(permanents, key=cle), sorted(vacataires, key=cle)
