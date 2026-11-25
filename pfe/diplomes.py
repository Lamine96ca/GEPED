"""
Registre des diplômés (modèle Diplome) : import Excel par la scolarité, vérification publique, statistiques.

Import : une ligne = un diplôme. Les colonnes sont reconnues par leur nom, y compris celles des anciens fichiers
(« Mat », « Réf », « Nom », « Prénoms », « Date_Naiss », « Lieu_naiss », « Nationalité », « Mention »…). La feuille
est trouvée seule (la première dont une ligne d'en-tête contient le nom et le matricule ou l'INE).
Les valeurs absentes du fichier (programme, diplôme, grade, année, date de délivrance…) peuvent être données une
fois pour tout le fichier. Un numéro déjà enregistré est ignoré (jamais en double). Le numéro est facultatif : un
diplômé connu seulement par la note de service est enregistré sans numéro (une fois par matricule ou INE).

Vérification publique : la structure donne son nom (et un contact), puis le matricule OU l'INE du diplômé ; tous
ses diplômes sont affichés. 10 échecs depuis un même appareil : 15 minutes d'attente ; 30 recherches par heure.
"""
import hashlib
import io
import re
import time
import unicodedata
from collections import defaultdict

from django.core.cache import cache
from django.db import transaction

from .importer import en_date, reparer_xlsx, texte
from .models import Diplome, Etudiant, Service, VerificationDiplome

OBLIGATOIRES = ['numero', 'nom']
COLONNES = ['numero', 'matricule', 'ine', 'civilite', 'sexe', 'nom', 'prenoms', 'date_naissance', 'annee_naissance',
            'lieu_naissance',
            'nationalite', 'programme', 'intitule', 'grade', 'finalite', 'mention', 'moyenne', 'annee_universitaire',
            'promotion', 'lieu_delivrance', 'date_delivrance', 'date_pv', 'chef_departement', 'directeur_general',
            'format']
ALIAS = {
    'ref': 'numero', 'reference': 'numero', 'num': 'numero', 'numero_du_diplome': 'numero',
    'numero_diplome': 'numero', 'num_diplome': 'numero', 'n_diplome': 'numero', 'no_diplome': 'numero',
    'mat': 'matricule', 'matricules': 'matricule', 'mme_m': 'civilite', 'mme_m_': 'civilite',
    'prenom': 'prenoms', 'noms': 'nom', 'nom_de_famille': 'nom',
    'date_naiss': 'date_naissance', 'date_de_naissance': 'date_naissance', 'ne_le': 'date_naissance',
    'ne_e_le': 'date_naissance', 'ne_en': 'annee_naissance', 'ne_e_en': 'annee_naissance',
    'annee_de_naissance': 'annee_naissance', 'annee_naiss': 'annee_naissance', 'lieu_naiss': 'lieu_naissance', 'lieu_de_naissance': 'lieu_naissance',
    'nationalites': 'nationalite', 'option': 'programme', 'filiere': 'programme', 'specialite': 'programme',
    'diplome': 'intitule', 'mg': 'moyenne', 'moyenne_generale': 'moyenne', 'm_g': 'moyenne',
    'annee': 'annee_universitaire', 'annee_academique': 'annee_universitaire', 'session': 'annee_universitaire',
    'fait_a': 'lieu_delivrance', 'date': 'date_delivrance', 'fait_le': 'date_delivrance',
    'date_de_delivrance': 'date_delivrance', 'delivre_le': 'date_delivrance', 'date_deliberation': 'date_pv',
    'date_du_pv': 'date_pv', 'chef_de_departement': 'chef_departement', 'directeur': 'directeur_general',
}
DEFAUTS = ['programme', 'intitule', 'grade', 'finalite', 'annee_universitaire', 'promotion', 'lieu_delivrance',
           'date_delivrance', 'date_pv', 'chef_departement', 'directeur_general']
ESSAIS_APPAREIL, ATTENTE = 10, 15 * 60
RECHERCHES_HEURE = 30                    # recherches par heure et par appareil (page publique)


def _sans_accent(t):
    return unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode()


def entete(t):
    e = re.sub(r'[^a-z0-9]+', '_', _sans_accent(t).strip().lower()).strip('_')
    return ALIAS.get(e, e)


def cle_texte(t):
    return re.sub(r'[^a-z0-9]', '', _sans_accent(t).lower())


def _lignes(fichier):
    """(entêtes normalisés, lignes) de la première feuille qui contient le tableau des diplômés."""
    import csv
    import openpyxl
    nom = fichier.name.lower()
    fichier.seek(0)
    contenu = fichier.read()
    feuilles = []
    if nom.endswith('.csv'):
        t = contenu.decode('utf-8-sig', errors='replace')
        try:
            dialecte = csv.Sniffer().sniff(t[:4096], delimiters=';,\t')
        except csv.Error:
            dialecte = csv.excel
            dialecte.delimiter = ';'
        feuilles = [list(csv.reader(io.StringIO(t), dialecte))]
    elif nom.endswith('.xlsx'):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
        except Exception:
            try:
                wb = openpyxl.load_workbook(io.BytesIO(reparer_xlsx(contenu)[0]), read_only=True, data_only=True)
            except Exception:
                raise ValueError("Ce fichier Excel est endommagé : ouvrez-le dans Excel, « Enregistrer sous » .xlsx.")
        feuilles = [list(ws.iter_rows(values_only=True)) for ws in wb.worksheets]
    else:
        raise ValueError("Format non pris en charge : utilisez un fichier .xlsx ou .csv.")
    # d'abord une feuille avec la colonne du numéro ; sinon la première qui a le nom et le matricule ou l'INE
    for avec_numero in (True, False):
        for lignes in feuilles:
            for k, ligne in enumerate(lignes[:20]):
                entetes = [entete(c) for c in ligne]
                if ('nom' in entetes and ('matricule' in entetes or 'ine' in entetes)
                        and ('numero' in entetes or not avec_numero)):
                    return entetes, lignes[k + 1:], k + 2
    raise ValueError("Aucune feuille ne contient le tableau des diplômés : il faut au moins les colonnes nom, et "
                     "matricule ou INE (le numéro du diplôme, « Réf », est facultatif).")


class Rapport:
    def __init__(self, simulation):
        self.simulation = simulation
        self.lignes = self.crees = 0
        self.deja, self.erreurs, self.colonnes_ignorees, self.non_rattaches = [], [], [], 0


def annee_seule(v):
    """1988, « 1988 », « vers 1988 », « né(e) en 1988 » → 1988 ; sinon None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if 1900 <= v <= 2100 and v == int(v) else None
    m = re.fullmatch(r'(?:n[ée]+\s*(?:\(e\))?\s*en\s*|vers\s*)?((?:19|20)\d\d)', texte(v).strip().lower())
    return int(m.group(1)) if m else None


def _valeur(champ, v):
    if champ == 'annee_naissance':
        if not texte(v):
            return None
        a = annee_seule(v)
        if a is None:
            raise ValueError(f"année de naissance invalide « {texte(v)} »")
        return a
    if champ == 'date_naissance' and annee_seule(v):
        return None                                                # année seule : voir annee_naissance
    if champ in ('date_naissance', 'date_delivrance', 'date_pv'):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 1900 <= v <= 2100:
            return None                                            # année seule : date inconnue
        return en_date(v)
    if champ == 'moyenne':
        s = texte(v).replace(',', '.')
        if not s:
            return None
        m = float(s)
        if not 0 <= m <= 20:
            raise ValueError(f"moyenne invalide « {s} »")
        return round(m, 2)
    return ' '.join(texte(v).split())


def _sexe(civilite, sexe):
    c, s = cle_texte(civilite), cle_texte(sexe)
    if s in ('f', 'feminin', 'femme') or c in ('mme', 'mlle', 'madame', 'mademoiselle'):
        return 'F'
    if s in ('m', 'masculin', 'homme') or c in ('m', 'mr', 'monsieur'):
        return 'M'
    return ''


def importer(fichier, defauts, utilisateur, simulation=True):
    """Importe le registre ; `defauts` : valeurs pour tout le fichier (programme, intitulé…), `format` : 'auto'…"""
    rapport = Rapport(simulation)
    entetes, lignes, premiere = _lignes(fichier)
    rapport.colonnes_ignorees = sorted({e for e in entetes if e and e not in COLONNES and e not in ('n', 'no')})
    programmes = {cle_texte(s.nom): s for s in Service.objects.filter(programme=True)}
    existants = set(Diplome.objects.exclude(numero_cle='').values_list('numero_cle', flat=True))
    # sans numéro (diplômé connu par la note de service) : un seul diplôme par matricule ou INE
    sans_numero = {('mat', cle_matricule(m)) for m in Diplome.objects.filter(numero__isnull=True).exclude(matricule='')
                   .values_list('matricule', flat=True)}
    sans_numero |= {('ine', i) for i in Diplome.objects.filter(numero__isnull=True).exclude(ine='')
                    .values_list('ine', flat=True)}
    vus, a_creer = set(), []
    for num, brut in enumerate(lignes, start=premiere):
        ligne = {e: v for e, v in zip(entetes, brut) if e in COLONNES}
        if not any(texte(v) for v in ligne.values()):
            continue
        rapport.lignes += 1
        if annee_seule(ligne.get('date_naissance')) and not texte(ligne.get('annee_naissance')):
            ligne['annee_naissance'] = annee_seule(ligne['date_naissance'])     # « Né(e) en 1988 »
        try:
            d = {c: _valeur(c, ligne.get(c)) for c in COLONNES if c in ligne}
        except ValueError as e:
            rapport.erreurs.append((num, str(e)))
            continue
        for c in DEFAUTS:
            if not d.get(c) and defauts.get(c):
                d[c] = defauts[c]
        if not d.get('nom'):
            rapport.erreurs.append((num, "nom manquant"))
            continue
        if not d.get('matricule') and not d.get('ine'):
            rapport.erreurs.append((num, "matricule ou INE manquant"))
            continue
        if d.get('numero'):
            cle = Diplome.cle(d['numero'])
            if cle in existants or cle in vus:
                rapport.deja.append((num, d['numero']))
                continue
            vus.add(cle)
        else:
            d.pop('numero', None)
            ids = {('mat', cle_matricule(d['matricule'])) if d.get('matricule') else None,
                   ('ine', re.sub(r'\s', '', d['ine']).upper()) if d.get('ine') else None} - {None}
            if ids & sans_numero:
                rapport.deja.append((num, d.get('matricule') or d.get('ine')))
                continue
            sans_numero |= ids
        fmt = cle_texte(d.pop('format', '') or defauts.get('format', ''))
        fmt = fmt if fmt in (Diplome.ANCIEN, Diplome.NOUVEAU) else (Diplome.NOUVEAU if d.get('ine') else Diplome.ANCIEN)
        etudiant = None
        if d.get('ine'):
            etudiant = Etudiant.objects.filter(ine__iexact=d['ine']).first()
        if etudiant is None and d.get('matricule'):
            etudiant = Etudiant.objects.filter(matricule=d['matricule']).first()
        rapport.non_rattaches += etudiant is None
        filiere = programmes.get(cle_texte(d.get('programme')))
        if filiere is None and etudiant is not None and not d.get('programme'):
            derniere = etudiant.etudiant_inscription.filter(filiere__isnull=False).order_by('-session__annee').first()
            filiere = derniere.filiere if derniere else None
        if not d.get('programme') and filiere is not None:
            d['programme'] = filiere.nom
        sexe = _sexe(d.get('civilite'), d.pop('sexe', '')) or (etudiant.sexe if etudiant else '')
        a_creer.append(Diplome(format=fmt, etudiant=etudiant, filiere=filiere, sexe=sexe or '', importe_par=utilisateur,
                               **{k: v for k, v in d.items() if v not in (None, '')}))
    if not simulation:
        with transaction.atomic():
            for x in a_creer:                       # save() : clé du numéro, année d'obtention
                x.save()
    rapport.crees = len(a_creer)
    return rapport


def modele_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Diplomes'
    colonnes = ['numero', 'matricule', 'ine', 'civilite', 'nom', 'prenoms', 'date_naissance', 'lieu_naissance',
                'nationalite', 'programme', 'intitule', 'grade', 'finalite', 'mention', 'moyenne',
                'annee_universitaire', 'promotion', 'lieu_delivrance', 'date_delivrance', 'date_pv']
    ws.append(colonnes)
    ws.append(['ISTM-1000000001', '', 'XYZA0101990001', 'M.', 'SOW', 'Mamadou', '01/01/1999', 'Mamou', '',
               'CONCEPTION ET FABRICATION MECANIQUE', '', 'LICENCE', 'PROFESSIONNELLE', 'Bien', '', '2024 - 2025',
               'P16', 'Mamou', '26/03/2026', '21/07/2025'])
    ws.append(['AB/06/18/0001', '1500000001', '', '', 'DIALLO', 'Fanta', '11/11/1990', 'Abidjan',
               'Guinéenne', 'CONCEPTION ET FABRICATION MECANIQUE', 'Licence en ingénierie (premier cycle LMD)', '',
               '', 'Très Bien', 7.43, '2014 - 2015', 'P10', 'Mamou', '18/06/2018', ''])
    for cell in ws[1]:
        obligatoire = cell.value in ('nom', 'matricule', 'ine')
        cell.font = Font(bold=True, color='1F2937' if obligatoire else 'FFFFFF')
        cell.fill = PatternFill('solid', fgColor='FBBD08' if obligatoire else '41627C')
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(14, max(len(str(c.value or '')) for c in col) + 2)
    ws.freeze_panes = 'A2'
    flux = io.BytesIO()
    wb.save(flux)
    return flux.getvalue()


# ------------------------------------------------------------------ vérification publique

def _appareil(request):
    relais = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[-1].strip()
    brut = f"{request.META.get('REMOTE_ADDR', '') or 'inconnue'}|{relais[:45]}"
    return 'diplome:' + hashlib.sha256(brut.encode()).hexdigest()[:32]


def bloque(request):
    d = cache.get(_appareil(request))
    if d and d['n'] >= ESSAIS_APPAREIL:
        return max(1, int(d['depuis'] + ATTENTE - time.time()))
    t = cache.get(_appareil(request) + ':total')
    if t and t['n'] >= RECHERCHES_HEURE:
        return max(1, int(t['depuis'] + 3600 - time.time()))
    return 0


def _compter(request):
    """Toutes les recherches (trouvées ou non) : au plus RECHERCHES_HEURE par heure et par appareil."""
    cle = _appareil(request) + ':total'
    t = cache.get(cle) or {'n': 0, 'depuis': time.time()}
    t['n'] += 1
    cache.set(cle, t, max(1, int(t['depuis'] + 3600 - time.time())))


def _echec(request):
    cle = _appareil(request)
    d = cache.get(cle) or {'n': 0, 'depuis': time.time()}
    d['n'] += 1
    d['depuis'] = time.time()
    cache.set(cle, d, ATTENTE)


def cle_matricule(m):
    """Matricule sans espaces ni zéros de tête (Excel les perd : 902014402 = 0902014402)."""
    return re.sub(r'^[0O]+', '', re.sub(r'\s+', '', str(m or '')).upper())


def verifier(request, structure, contact, identifiant):
    """Cherche les diplômes du matricule OU de l'INE, enregistre la vérification ; renvoie (résultat, diplômes)."""
    _compter(request)
    ident = re.sub(r'\s+', '', identifiant or '').upper()
    diplomes = list(Diplome.objects.filter(models_q(ident)).select_related('filiere')
                    .order_by('annule', '-annee_obtention')) if len(ident) >= 4 else []
    if not diplomes:
        resultat = VerificationDiplome.INTROUVABLE
        _echec(request)
    else:
        resultat = (VerificationDiplome.ANNULE if all(d.annule for d in diplomes)
                    else VerificationDiplome.AUTHENTIQUE)
    VerificationDiplome.objects.create(structure=structure[:150], contact=(contact or '')[:150],
                                       identifiant=(identifiant or '')[:40],
                                       numero=(diplomes[0].numero or '')[:60] if diplomes else '',
                                       resultat=resultat, diplome=diplomes[0] if diplomes else None)
    return resultat, diplomes


def models_q(ident):
    from django.db.models import Q
    m = cle_matricule(ident)
    variantes = {ident, m, '0' + m, 'O' + m} if m else {ident}
    return Q(ine=ident) | Q(matricule__in=variantes)


def infos_publiques(d):
    """Informations écrites sur le diplôme, dans l'ordre du document (rien d'autre : ni moyenne, ni fiche)."""
    infos = [("Titulaire", f"{(d.civilite + ' ') if d.civilite else ''}{d.nom} {d.prenoms}".strip())]
    if d.matricule:
        infos.append(('Matricule', d.matricule))
    if d.ine:
        infos.append(('INE', d.ine))
    if d.date_naissance or d.annee_naissance or d.lieu_naissance:
        quand = (d.date_naissance.strftime('%d/%m/%Y') if d.date_naissance
                 else str(d.annee_naissance) if d.annee_naissance else '')
        infos.append(('Né(e) le' if d.date_naissance else 'Né(e) en' if d.annee_naissance else 'Né(e) à',
                      ' à '.join(x for x in (quand, d.lieu_naissance) if x)))
    if d.nationalite:
        infos.append(('Nationalité', d.nationalite))
    for libelle, valeur in (('Diplôme', d.intitule), ('Grade', d.grade), ('Programme', d.programme),
                            ('Finalité', d.finalite), ('Mention', d.mention),
                            ('Année universitaire', d.annee_universitaire), ('Promotion', d.promotion)):
        if valeur:
            infos.append((libelle, valeur))
    if d.date_delivrance:
        infos.append(('Délivré', f"{d.lieu_delivrance + ', le ' if d.lieu_delivrance else 'le '}"
                                 f"{d.date_delivrance.strftime('%d/%m/%Y')}"))
    return infos


# ------------------------------------------------------------------ statistiques

TRANCHES_AGE = [(0, 24, 'Moins de 25 ans'), (25, 29, '25 à 29 ans'), (30, 34, '30 à 34 ans'),
                (35, 39, '35 à 39 ans'), (40, 44, '40 à 44 ans'), (45, 200, '45 ans et plus')]
AGE_MIN, AGE_MAX = 15, 90                 # en dehors : année de naissance incohérente, écartée


def statistiques(qs):
    """
    Par année d'obtention : diplômés (H, F, non précisé), annulés et vérifications reçues ; par programme ;
    par tranche d'âge ACTUEL (année en cours − année de naissance, même quand seule l'année est connue).
    """
    annees = defaultdict(lambda: {'h': 0, 'f': 0, 'x': 0, 'annules': 0, 'verifications': 0})
    programmes = defaultdict(int)
    tranches = {lib: {'h': 0, 'f': 0, 'x': 0} for _, _, lib in TRANCHES_AGE}
    inconnu = {'h': 0, 'f': 0, 'x': 0}
    somme_ages, nb_ages, incoherents = 0, 0, 0
    import datetime
    cette_annee = datetime.date.today().year
    for annee, sexe, annule, programme, naissance in qs.values_list('annee_obtention', 'sexe', 'annule', 'programme',
                                                                     'annee_naissance'):
        cle_sexe = 'h' if sexe == 'M' else 'f' if sexe == 'F' else 'x'
        a = annees[annee]
        a[cle_sexe] += 1
        a['annules'] += annule
        programmes[programme or '—'] += 1
        age = cette_annee - naissance if naissance else None
        if age is not None and not AGE_MIN <= age <= AGE_MAX:
            incoherents += 1
            age = None
        if age is None:
            inconnu[cle_sexe] += 1
            continue
        somme_ages += age
        nb_ages += 1
        for mini, maxi, lib in TRANCHES_AGE:
            if mini <= age <= maxi:
                tranches[lib][cle_sexe] += 1
                break
    from django.db.models import Count
    for annee, n in (VerificationDiplome.objects.filter(diplome__in=qs).values('diplome__annee_obtention')
                     .annotate(n=Count('id')).values_list('diplome__annee_obtention', 'n')):
        annees[annee]['verifications'] += n
    lignes = []
    for annee in sorted(annees, key=lambda a: (a is None, -(a or 0))):
        a = annees[annee]
        lignes.append({'annee': annee or 'Non précisée', **a, 'total': a['h'] + a['f'] + a['x']})
    total = {k: sum(l[k] for l in lignes) for k in ('h', 'f', 'x', 'annules', 'verifications', 'total')}
    ages = [{'tranche': lib, **t, 'total': sum(t.values())} for lib, t in tranches.items()]
    if sum(inconnu.values()):
        ages.append({'tranche': 'Âge inconnu', **inconnu, 'total': sum(inconnu.values()), 'inconnu': True})
    for l in ages:
        l['pct'] = round(100 * l['total'] / total['total'], 1) if total['total'] else 0
    return {'annees': lignes, 'total': total,
            'programmes': sorted(programmes.items(), key=lambda p: (-p[1], p[0])),
            'ages': ages, 'age_moyen': round(somme_ages / nb_ages, 1) if nb_ages else None, 'ages_connus': nb_ages,
            'ages_incoherents': incoherents, 'annee_age': cette_annee,
            'taux_age': round(100 * nb_ages / total['total'], 1) if total['total'] else 0}


def sections_pdf(s):
    """Sections PDF des statistiques des diplômés : par année d'obtention, puis par tranche d'âge."""
    from .pdf_listes import cellule, section
    n = lambda v: f"{v:,}".replace(',', ' ')                                             # noqa: E731

    def rang(lib, l, extra):
        return [str(lib), cellule(n(l['h']), align='d'), cellule(n(l['f']), align='d'), cellule(n(l['x']), align='d'),
                cellule(n(l['total']), align='d', gras=True)] + [cellule(e, align='d') for e in extra]
    t = s['total']
    annees = section("Par année d'obtention", [("Année d'obtention", 110), ('Hommes', 55), ('Femmes', 55),
                                               ('Non précisé', 65), ('Total', 55), ('Annulés', 55),
                                               ('Vérifications', 70)],
                     [rang(l['annee'], l, [n(l['annules']), n(l['verifications'])]) for l in s['annees']],
                     pied=rang('Total', t, [n(t['annules']), n(t['verifications'])]) if s['annees'] else None,
                     vide='Aucun diplôme.')
    note = (f"Âge en {s['annee_age']} (année en cours − année de naissance ; l'année seule suffit) · âge moyen : "
            f"{s['age_moyen'] or '—'} ans"
            f" · âge connu : {s['ages_connus']} diplômé(s), {str(s['taux_age']).replace('.', ',')} %")
    if s['ages_incoherents']:
        note += f" · {s['ages_incoherents']} année(s) de naissance incohérente(s) écartée(s)"
    ages = section(f"Âge des diplômés en {s['annee_age']}", [("Tranche d'âge", 110), ('Hommes', 55), ('Femmes', 55),
                                                    ('Non précisé', 65), ('Total', 55), ('%', 55)],
                   [rang(l['tranche'], l, [f"{l['pct']} %".replace('.', ',')]) for l in s['ages']],
                   pied=rang('Total', t, ['100 %']) if s['ages'] else None, note=note, vide='Aucun diplôme.')
    return [annees, ages]
