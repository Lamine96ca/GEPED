"""
Heures des enseignants : prévues (emploi du temps), faites (cours validés par un délégué), équivalent TD, charge
statutaire et heures supplémentaires, selon l'arrêté A/2024/108/MESRSI/SGG et le décret D/2024/0027 (valeurs dans
ParametresHeures, voir son docstring) ; aucune autre règle.

- Prévu : heures par semaine de l'emploi du temps × semaines (du semestre, ou écoulées à ce jour d'après les dates
  des semestres saisies par la Direction des Études).
- Fait : somme des durées (départ − arrivée, données par le délégué) des cours VALIDÉS (service fait, dûment
  constaté), converties en équivalent TD (CM × 1,5 ; TP × 2/3 ; un contrôle compte comme une heure de TD).
- Permanents : heures supplémentaires sur l'ANNÉE, au-delà de la charge statutaire annuelle (voir situation()).
- Vacataires : vacation = heures faites × taux du grade, sans charge ni plafond.
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
    """Ajoute à chaque ligne les largeurs (%) des barres prévu / fait, à l'échelle de la plus grande valeur (qui occupe
    80 % de la piste : la valeur écrite au bout de la barre reste visible)."""
    maximum = (max([max(l['a_ce_jour'] if l['a_ce_jour'] is not None else l['semestre'], l['fait']) for l in lignes]
                   or [0]) or 1) / 0.8
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


# ------------------------------------------------------------------ situation des enseignants (arrêté et décret)

def situation(session, enseignants=None, params=None):
    """
    (permanents, vacataires) de l'ANNÉE (session), une ligne par enseignant actif qui a des cours dans l'emploi du
    temps ou des cours validés. Strictement l'arrêté A/2024/108/MESRSI/SGG et le décret D/2024/0027 :

    Permanents (Professeur, Maître de conférences, Maître-assistant, Assistant) :
      1. heures faites = cours validés par un délégué (service fait, dûment constaté), converties en équivalent TD :
         CM × 1,5 ; TD × 1 ; TP × 2/3 (art. 3) ;
      2. charge statutaire annuelle du grade (art. 5), réduite de moitié pour une fonction administrative (art. 6) ;
      3. heures supplémentaires = ce qui dépasse la charge (art. 8), au plus la moitié de la charge (art. 8) et au
         plus 6 h par semaine (décret, art. 16 : on ne compte, chaque semaine, que 6 h au-delà de la charge de la
         semaine) ;
      4. montant = heures supplémentaires × taux du grade (décret, art. 16).
    Vacataires : vacation = heures faites (validées) × taux du grade (décret, art. 16), sans charge ni plafond.
    Grades hors des textes (docteur, ingénieur…) : pas de charge ni de taux, signalé.
    """
    params = params or ParametresHeures.actuels()
    qs = Enseignant.objects.filter(actif=True).select_related('departement')
    if enseignants is not None:
        qs = qs.filter(pk__in=[e.pk for e in enseignants])
    ens = {e.pk: e for e in qs}
    # Prévu : emploi du temps des deux périodes, × semaines du semestre
    prevu = defaultdict(lambda: defaultdict(float))                 # {e_id: {'impair'|'pair': h par semaine}}
    for code, _ in PERIODES:
        for (e_id, _m), types in hebdo_edt(session, code, list(ens.values())).items():
            prevu[e_id][code] += sum(types.values())
    # Fait : cours validés de l'année
    fait = defaultdict(lambda: {'types': defaultdict(float), 'semaines': defaultdict(float),
                                'par_periode': defaultdict(float)})
    seances = (SeanceCours.objects.filter(matiere__session=session, statut=SeanceCours.VALIDEE,
                                          enseignant__in=list(ens.values())).select_related('matiere__semestre'))
    for s in seances:
        h = s.duree or 0
        f = fait[s.enseignant_id]
        f['types'][s.type] += h
        eq = h * params.coefficient(s.type)
        f['semaines'][tuple(s.date.isocalendar()[:2])] += eq
        f['par_periode']['impair' if emplois.periode(s.matiere.semestre) == 1 else 'pair'] += eq
    permanents, vacataires = [], []
    for e_id in set(prevu) | set(fait):
        e = ens.get(e_id)
        if e is None:
            continue
        f = fait.get(e_id) or {'types': {}, 'semaines': {}, 'par_periode': {}}
        reel = sum(f['types'].values())
        eq_td = sum(h * params.coefficient(t) for t, h in f['types'].items())
        edt_annee = sum(h * params.semaines_semestre for h in prevu.get(e_id, {}).values())
        g = params.grade(e.grade)
        ligne = {'enseignant': e, 'grade': e.get_grade_display() or '—', 'reel': reel, 'eq_td': eq_td,
                 'edt_annee': edt_annee, 'eq_impair': f['par_periode'].get('impair', 0),
                 'eq_pair': f['par_periode'].get('pair', 0), 'taux': g['taux'], 'alertes': [],
                 'permanent': e.statut != Enseignant.VACATAIRE,
                 'par_type': [(SeanceCours.COURTS.get(t, t), h, h * params.coefficient(t))
                              for t, h in sorted(f['types'].items())]}
        if e.statut == Enseignant.VACATAIRE:
            ligne['montant'] = reel * g['taux'] if g['taux'] else None
            if not g['taux']:
                ligne['alertes'].append("grade hors du décret : pas de taux de vacation")
            vacataires.append(ligne)
            continue
        if g['charge'] is None:
            ligne.update({'charge': None, 'hs': None, 'montant': None})
            ligne['alertes'].append("grade hors de l'arrêté : pas de charge statutaire")
            permanents.append(ligne)
            continue
        reduite = bool(e.fonction_administrative and params.reduction_fonction)
        charge = float(g['charge']) * (1 - (params.reduction_fonction / 100 if reduite else 0))
        charge_semaine = charge / params.semaines_annee
        depassement = max(0.0, eq_td - charge)                       # art. 8 : après la charge épuisée
        plafond_charge = charge * params.hs_plafond_pct / 100         # art. 8 : la moitié de la charge
        plafond_semaines = sum(min(float(params.hs_plafond_hebdo), max(0.0, h - charge_semaine))
                               for h in f['semaines'].values())       # décret : 6 h par semaine au plus
        hs = min(depassement, plafond_charge, plafond_semaines)
        if hs < depassement:
            ligne['alertes'].append(
                f"{fmt(depassement - hs)} au-delà de la charge non comptées "
                + ("(plafond : la moitié de la charge)" if hs == plafond_charge
                   else f"(plafond : {params.hs_plafond_hebdo:g} h par semaine)"))
        ligne.update({'charge': charge, 'charge_texte': float(g['charge']), 'reduite': reduite,
                      'charge_semaine': charge_semaine, 'depassement': depassement, 'plafond_charge': plafond_charge,
                      'plafond_semaines': plafond_semaines, 'hs': hs, 'reste': max(0.0, charge - eq_td),
                      'montant': hs * g['taux'] if g['taux'] else None,
                      'semaines_dessus': sum(1 for h in f['semaines'].values() if h > charge_semaine),
                      'semaines_detail': [
                          {'lundi': _date.fromisocalendar(an, sem, 1), 'eq_td': h, 'eq_td_txt': fmt(h),
                           'au_dela': max(0.0, h - charge_semaine), 'au_dela_txt': fmt(max(0.0, h - charge_semaine)),
                           'compte': min(float(params.hs_plafond_hebdo), max(0.0, h - charge_semaine)),
                           'compte_txt': fmt(min(float(params.hs_plafond_hebdo), max(0.0, h - charge_semaine)))}
                          for (an, sem), h in sorted(f['semaines'].items())]})
        if e.grade in RANG_A and params.cm_rang_a_pct and eq_td:
            part_cm = f['types'].get(SeanceCours.CM, 0) * params.coefficient(SeanceCours.CM) / eq_td * 100
            if part_cm < params.cm_rang_a_pct:
                ligne['alertes'].append(f"rang A : {round(part_cm)} % de CM (au moins {params.cm_rang_a_pct} %, "
                                        f"arrêté art. 5)")
        permanents.append(ligne)
    for l in permanents + vacataires:
        for k in ('reel', 'eq_td', 'edt_annee', 'eq_impair', 'eq_pair', 'charge', 'charge_semaine', 'hs', 'reste',
                  'depassement', 'plafond_charge', 'plafond_semaines'):
            if k in l:
                l[k + '_txt'] = fmt(l[k])
        l['montant_txt'] = gnf(l.get('montant'))
        l['taux_txt'] = gnf(l['taux']) if l.get('taux') else '—'
        l['par_type_txt'] = [(t, fmt(h), fmt(eq)) for t, h, eq in l['par_type']]
        reference = max(l['edt_annee'], l.get('charge') or 0, l['eq_td'], 1) / 0.8
        l['barre_prevu'] = round(100 * l['edt_annee'] / reference, 1)
        l['barre_fait'] = round(100 * (l['eq_td'] if l['permanent'] else l['reel']) / reference, 1)
        l['repere_charge'] = round(100 * l['charge'] / reference, 1) if l.get('charge') else None
    cle = lambda l: (l['enseignant'].departement.nom if l['enseignant'].departement_id else '', l['enseignant'].nom)  # noqa: E731
    return sorted(permanents, key=cle), sorted(vacataires, key=cle)
