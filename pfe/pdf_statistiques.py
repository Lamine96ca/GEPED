"""
Impression (PDF) des statistiques des inscriptions, onglet par onglet (celui affiché à l'écran).

Chaque onglet donne une ou plusieurs sections (pfe/pdf_listes.py) : les mêmes tableaux que la page, avec leur
ligne de total ; l'onglet « Évolution » ajoute un graphique en barres hommes / femmes par session.
En-tête de l'IST, filtres choisis dans le titre, signature du chef du service (comme les autres listes).
"""
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors

from .pdf_listes import BLEU, cellule, section

ONGLETS = {
    'croise': 'Programme × niveau', 'evolution': 'Évolution des inscriptions', 'promotions': 'Par promotion',
    'feminisation': 'Taux de féminisation par programme', 'ages': "Tranches d'âge", 'origine': 'Origine scolaire',
    'redoublants': 'Redoublants', 'handicap': 'Situation de handicap', 'reussite': 'Réussite',
    'soutenances': 'Soutenances', 'diplomes': 'Diplômés',
}
ROSE = colors.HexColor('#F5B700')


def _n(v):
    """1234 → « 1 234 »."""
    return f"{v:,}".replace(',', ' ') if isinstance(v, int) else (v if v not in (None, '') else '—')


def _pc(v):
    """17.3 → « 17,3 % »."""
    return f"{v} %".replace('.', ',')


def _hf(titre, lignes_total, titre_colonne, note='', taux=None):
    """Section Hommes / Femmes / Total / % F (et une colonne de taux)."""
    lignes, total = lignes_total
    colonnes = [(titre_colonne, 170), ('Hommes', 50), ('Femmes', 50), ('Total', 50), ('% F', 44)]
    if taux:
        colonnes.append((taux, 60))

    def rang(l):
        r = [l.libelle, cellule(_n(l.h), align='d'), cellule(_n(l.f), align='d'),
             cellule(_n(l.total), align='d', gras=True), cellule(_pc(l.pct_f), align='d')]
        return r + ([cellule(_pc(l.extra.get('taux', 0)), align='d')] if taux else [])
    return section(titre, colonnes, [rang(l) for l in lignes], pied=rang(total) if lignes else None, note=note)


def _graphique_evolution(evolution, largeur):
    """Barres empilées hommes / femmes par session."""
    hauteur = 150
    d = Drawing(largeur, hauteur + 20)
    if not evolution:
        return d
    graphe = VerticalBarChart()
    graphe.x, graphe.y, graphe.width, graphe.height = 30, 25, largeur - 40, hauteur - 20
    graphe.data = [[l.h for l in evolution], [l.f for l in evolution]]
    graphe.categoryAxis.style = 'stacked'
    graphe.categoryAxis.categoryNames = [f"{l.libelle[2:4]}-{l.libelle[-2:]}" for l in evolution]
    graphe.categoryAxis.labels.fontSize = 6.5
    graphe.valueAxis.labels.fontSize = 6.5
    graphe.valueAxis.valueMin = 0
    graphe.bars[0].fillColor, graphe.bars[1].fillColor = BLEU, ROSE
    graphe.bars.strokeColor = None
    d.add(graphe)
    d.add(String(32, hauteur + 6, 'Hommes', fontSize=7, fillColor=BLEU))
    d.add(String(75, hauteur + 6, 'Femmes', fontSize=7, fillColor=colors.HexColor('#B58700')))
    return d


def sections_onglet(onglet, s, largeur_utile):
    """Sections PDF de l'onglet `onglet` ; `s` : statistiques calculées (pfe/statistiques.py)."""
    if onglet == 'croise':
        c = s['croise']
        colonnes = [('Programme', 150)] + [(n, 52) for n in c['niveaux']] + [('Total', 56)]

        def case(x, gras=False):
            return cellule(_n(x.total), f"{x.h} H / {x.f} F", align='c', gras=gras) if x else cellule('—', align='c')
        lignes = [[l['programme']] + [case(x) for x in l['cellules']] + [case(l['total'], True)] for l in c['lignes']]
        pied = (['Total'] + [case(x) for x in c['totaux']] + [case(c['total'])]) if c['lignes'] else None
        return [section('', colonnes, lignes, pied, note='Dans chaque case : total, puis hommes / femmes.')]
    if onglet == 'evolution':
        lignes = [[l.libelle, cellule(_n(l.h), align='d'), cellule(_n(l.f), align='d'),
                   cellule(_n(l.total), align='d', gras=True), cellule(_pc(l.pct_f), align='d'),
                   cellule(_n(l.extra['nouveaux']), align='d'), cellule(_n(l.extra['reinscrits']), align='d')]
                  for l in s['evolution']]
        return [section('', [('Session', 90), ('Hommes', 55), ('Femmes', 55), ('Total', 55), ('% F', 45),
                             ('Nouveaux inscrits', 70), ('Réinscrits', 60)], lignes,
                        note="Toutes les sessions (filtres cycle et programme appliqués). Nouveaux inscrits : "
                             "LICENCE 1, 1ère Année (et M1) ; réinscrits : les autres niveaux.",
                        avant=[_graphique_evolution(s['evolution'], largeur_utile)])]
    if onglet == 'promotions':
        return [_hf('', s['promotions'], 'Promotion')]
    if onglet == 'feminisation':
        return [_hf('', (s['feminisation'], s['programmes'][1]), 'Programme',
                    note=f"Du plus au moins féminisé · moyenne : {_pc(s['programmes'][1].pct_f)} de femmes.")]
    if onglet == 'ages':
        a = s['ages']
        note = (f"Âge au 31 décembre de la 1re année de la session · âge moyen : {a['moyenne'] or '—'} ans (hommes "
                f"{a['moyenne_h'] or '—'}, femmes {a['moyenne_f'] or '—'}) · date de naissance renseignée : "
                + _pc(a['taux']))
        if a['incoherentes']:
            note += f" · {a['incoherentes']} date(s) incohérente(s) écartée(s)"
        return [_hf('', (a['lignes'], a['total']), "Tranche d'âge", note=note)]
    if onglet == 'origine':
        o = s['origine']
        secs = [_hf(titre, (o[cle]['lignes'], o[cle]['total']), colonne, note="Renseigné : " + _pc(o[cle]['taux']))
                for titre, cle, colonne in (('Option du bac', 'options', 'Option'),
                                            ('Nationalité', 'nationalites', 'Nationalité'),
                                            ('Centres du bac (15 premiers)', 'centres', 'Centre'))]
        secs.append(_hf("Étudiants exclus d'un programme", (o['exclus']['lignes'], o['exclus']['total']), 'Programme',
                        taux="Taux d'exclusion", note="Exclusions en vigueur ; taux parmi les inscrits du programme"))
        secs.append(section('Moyenne au bac par programme', [('Programme', 220), ('Moyenne au bac', 70),
                                                             ('Étudiants renseignés', 80)],
                            [[b['programme'], cellule(b['moyenne'], align='d', gras=True), cellule(_n(b['n']), align='d')]
                             for b in o['bac']], note="Renseigné : " + _pc(o['taux_bac']),
                            vide='Aucune moyenne au bac renseignée.'))
        return secs
    if onglet == 'redoublants':
        r = s['redoublants']
        return [_hf('Redoublants par niveau', r['niveaux'], 'Niveau', taux='Taux de redoublement'),
                _hf('Redoublants par programme', r['programmes'], 'Programme', taux='Taux de redoublement')]
    if onglet == 'handicap':
        h = s['handicap']
        note = (f"{h['nb']} inscrit(s) en situation de handicap, soit {_pc(h['taux'])} des inscrits." if h['nb']
                else "Aucun inscrit n'est déclaré en situation de handicap pour ces filtres.")
        return [_hf('Par type de handicap', h['types'], 'Type', note=note),
                _hf('Par niveau', h['niveaux'], 'Niveau', taux='Part des inscrits'),
                _hf('Par programme', h['programmes'], 'Programme', taux='Part des inscrits')]
    if onglet == 'reussite':
        r = s['reussite']

        def rang(l):
            return [l.libelle, cellule(_n(l.total), align='d'),
                    cellule(_n(l.extra['admis_h'] + l.extra['admis_f']), align='d'),
                    cellule(_pc(l.extra['taux']), align='d', gras=True), cellule(_pc(l.extra['taux_h']), align='d'),
                    cellule(_pc(l.extra['taux_f']), align='d')]
        colonnes = [('', 190), ('Effectif', 55), ('Admis', 50), ('Taux', 50), ('Taux H', 50), ('Taux F', 50)]
        note = (f"D'après les résultats annuels calculés à partir des notes : admis = moyenne de l'année ≥ "
                f"{r['seuil']}/10 · {r['nb']} résultat(s).")
        return [section(titre, colonnes, [rang(l) for l in lignes], pied=rang(total) if lignes else None,
                        note=note if k == 0 else '', vide='Aucun résultat calculé pour ces filtres.')
                for k, (titre, (lignes, total)) in enumerate((('Réussite par programme', r['programmes']),
                                                              ('Réussite par niveau', r['niveaux'])))]
    if onglet == 'soutenances':
        so = s['soutenances']

        def rang(l):
            return [l.libelle, cellule(_n(l.h), align='d'), cellule(_n(l.f), align='d'),
                    cellule(_n(l.total), align='d', gras=True), cellule(_n(l.extra['notees']), align='d'),
                    cellule(_n(l.extra['validees']), align='d'), cellule(_pc(l.extra['taux']), align='d'),
                    cellule(l.extra['moyenne'] or '—', align='d')]
        return [section('', [('Programme', 180), ('Hommes', 46), ('Femmes', 46), ('Total', 46), ('Notées', 46),
                             ('Validées', 46), ('Taux', 44), ('Moyenne', 50)],
                        [rang(l) for l in so['lignes']], pied=rang(so['total']) if so['lignes'] else None,
                        vide='Aucune soutenance pour ces filtres.')]
    if onglet == 'diplomes':
        from .diplomes import sections_pdf
        return sections_pdf(s['diplomes'])
    raise ValueError(onglet)
