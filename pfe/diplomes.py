"""
Registre des diplômés (modèle Diplome) : import Excel par la scolarité, vérification publique, statistiques.

Import : une ligne = un diplôme. Les colonnes sont reconnues par leur nom, y compris celles des anciens fichiers
(« Mat », « Réf », « Nom », « Prénoms », « Date_Naiss », « Lieu_naiss », « Nationalité », « Mention »…). La feuille
est trouvée seule (la première dont une ligne d'en-tête contient le numéro, le nom et le matricule ou l'INE).
Les valeurs absentes du fichier (programme, diplôme, grade, année, date de délivrance…) peuvent être données une
fois pour tout le fichier. Un numéro déjà enregistré est ignoré (jamais en double).

Vérification publique : la structure donne son nom, puis le matricule OU l'INE et le numéro du diplôme. Les deux
doivent correspondre au même diplôme. 10 échecs depuis un même appareil : 15 minutes d'attente.
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
COLONNES = ['numero', 'matricule', 'ine', 'civilite', 'sexe', 'nom', 'prenoms', 'date_naissance', 'lieu_naissance',
            'nationalite', 'programme', 'intitule', 'grade', 'finalite', 'mention', 'moyenne', 'annee_universitaire',
            'promotion', 'lieu_delivrance', 'date_delivrance', 'date_pv', 'chef_departement', 'directeur_general',
            'format']
ALIAS = {
    'ref': 'numero', 'reference': 'numero', 'num': 'numero', 'numero_du_diplome': 'numero',
    'numero_diplome': 'numero', 'num_diplome': 'numero', 'n_diplome': 'numero', 'no_diplome': 'numero',
    'mat': 'matricule', 'matricules': 'matricule', 'mme_m': 'civilite', 'mme_m_': 'civilite',
    'prenom': 'prenoms', 'noms': 'nom', 'nom_de_famille': 'nom',
    'date_naiss': 'date_naissance', 'date_de_naissance': 'date_naissance', 'ne_le': 'date_naissance',
    'ne_e_le': 'date_naissance', 'lieu_naiss': 'lieu_naissance', 'lieu_de_naissance': 'lieu_naissance',
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
    for lignes in feuilles:
        for k, ligne in enumerate(lignes[:20]):
            entetes = [entete(c) for c in ligne]
            if 'numero' in entetes and 'nom' in entetes and ('matricule' in entetes or 'ine' in entetes):
                return entetes, lignes[k + 1:], k + 2
    raise ValueError("Aucune feuille ne contient le tableau des diplômés : il faut au moins les colonnes numéro (ou "
                     "« Réf »), nom, et matricule ou INE.")


class Rapport:
    def __init__(self, simulation):
        self.simulation = simulation
        self.lignes = self.crees = 0
        self.deja, self.erreurs, self.colonnes_ignorees, self.non_rattaches = [], [], [], 0


def _valeur(champ, v):
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
    existants = set(Diplome.objects.values_list('numero_cle', flat=True))
    vus, a_creer = set(), []
    for num, brut in enumerate(lignes, start=premiere):
        ligne = {e: v for e, v in zip(entetes, brut) if e in COLONNES}
        if not any(texte(v) for v in ligne.values()):
            continue
        rapport.lignes += 1
        try:
            d = {c: _valeur(c, ligne.get(c)) for c in COLONNES if c in ligne}
        except ValueError as e:
            rapport.erreurs.append((num, str(e)))
            continue
        for c in DEFAUTS:
            if not d.get(c) and defauts.get(c):
                d[c] = defauts[c]
        if not d.get('numero') or not d.get('nom'):
            rapport.erreurs.append((num, "numéro du diplôme ou nom manquant"))
            continue
        if not d.get('matricule') and not d.get('ine'):
            rapport.erreurs.append((num, "matricule ou INE manquant"))
            continue
        cle = Diplome.cle(d['numero'])
        if cle in existants or cle in vus:
            rapport.deja.append((num, d['numero']))
            continue
        vus.add(cle)
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
        obligatoire = cell.value in ('numero', 'nom', 'matricule', 'ine')
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
    return max(0, int(d['depuis'] + ATTENTE - time.time())) if d and d['n'] >= ESSAIS_APPAREIL else 0


def _echec(request):
    cle = _appareil(request)
    d = cache.get(cle) or {'n': 0, 'depuis': time.time()}
    d['n'] += 1
    d['depuis'] = time.time()
    cache.set(cle, d, ATTENTE)


def verifier(request, structure, contact, identifiant, numero):
    """Cherche le diplôme (matricule OU INE, ET numéro), enregistre la vérification ; renvoie (résultat, diplôme)."""
    ident = re.sub(r'\s+', '', identifiant or '').upper()
    cle = Diplome.cle(numero)
    diplome = None
    if ident and cle:
        diplome = (Diplome.objects.filter(numero_cle=cle).filter(
            models_q(ident)).select_related('filiere').first())
    if diplome is None:
        resultat = VerificationDiplome.INTROUVABLE
        _echec(request)
    else:
        resultat = VerificationDiplome.ANNULE if diplome.annule else VerificationDiplome.AUTHENTIQUE
    VerificationDiplome.objects.create(structure=structure[:150], contact=(contact or '')[:150],
                                       identifiant=(identifiant or '')[:40], numero=(numero or '')[:60],
                                       resultat=resultat, diplome=diplome)
    return resultat, diplome


def models_q(ident):
    from django.db.models import Q
    return Q(ine=ident) | Q(matricule__iexact=ident)


def infos_publiques(d):
    """Informations écrites sur le diplôme, dans l'ordre du document (rien d'autre : ni moyenne, ni fiche)."""
    infos = [("Titulaire", f"{(d.civilite + ' ') if d.civilite else ''}{d.nom} {d.prenoms}".strip())]
    if d.matricule:
        infos.append(('Matricule', d.matricule))
    if d.ine:
        infos.append(('INE', d.ine))
    if d.date_naissance or d.lieu_naissance:
        infos.append(('Né(e) le', ' à '.join(x for x in (d.date_naissance.strftime('%d/%m/%Y') if d.date_naissance
                                                          else '', d.lieu_naissance) if x)))
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

def statistiques(qs):
    """Par année d'obtention : diplômés (H, F, non précisé), annulés et vérifications reçues ; puis par programme."""
    annees = defaultdict(lambda: {'h': 0, 'f': 0, 'x': 0, 'annules': 0, 'verifications': 0})
    programmes = defaultdict(int)
    for annee, sexe, annule, programme in qs.values_list('annee_obtention', 'sexe', 'annule', 'programme'):
        a = annees[annee]
        a['h' if sexe == 'M' else 'f' if sexe == 'F' else 'x'] += 1
        a['annules'] += annule
        programmes[programme or '—'] += 1
    from django.db.models import Count
    for annee, n in (VerificationDiplome.objects.filter(diplome__in=qs).values('diplome__annee_obtention')
                     .annotate(n=Count('id')).values_list('diplome__annee_obtention', 'n')):
        annees[annee]['verifications'] += n
    lignes = []
    for annee in sorted(annees, key=lambda a: (a is None, -(a or 0))):
        a = annees[annee]
        lignes.append({'annee': annee or 'Non précisée', **a, 'total': a['h'] + a['f'] + a['x']})
    total = {k: sum(l[k] for l in lignes) for k in ('h', 'f', 'x', 'annules', 'verifications', 'total')}
    return {'annees': lignes, 'total': total,
            'programmes': sorted(programmes.items(), key=lambda p: (-p[1], p[0]))}
