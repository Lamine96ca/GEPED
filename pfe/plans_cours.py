"""
Plans de cours, cours dispensés et délégués de classe (espace enseignant).

1. L'enseignant dépose le plan de cours de sa matière (document Word .docx). GePed le découpe en CHAPITRES (titres
   « Titre 1 », lignes « Chapitre 1 », « I. »… ou lignes d'un tableau « Semaine | Contenu | Heures ») et, dans chaque
   chapitre, en CONTENUS (sous-titres « Titre 2 », « 1.1 », puces, lignes). L'enseignant corrige, puis soumet.
2. Le chef de département ou le directeur du programme approuve le plan (ou le renvoie avec ce qu'il faut corriger).
3. Après chaque cours, l'enseignant ouvre le chapitre, coche les contenus dispensés, choisit le type (CM, TD, TP,
   contrôle, tout), la date, et signe : c'est un « cours dispensé » (SeanceCours). Les délégués de la classe (deux
   au plus) reçoivent un lien par e-mail, valable 72 h : l'un d'eux vérifie les contenus, ajuste au besoin l'heure
   d'arrivée et l'heure de départ de l'enseignant, et signe. Ces heures sont les heures faites (pfe/heures.py).
4. En cas de désaccord, la Direction des Études réajuste les heures après la procédure administrative : l'enseignant
   puis un délégué signent de nouveau.
"""
import re
import zipfile
from datetime import time, timedelta
from decimal import Decimal, InvalidOperation

from django.core import signing

SEL_LIEN = 'gped-seance-cours'
DELAI_DELEGUE_HEURES = 72                  # le délégué valide dans les 72 h (expire_le) ; le jeton dure plus longtemps
VALIDITE_JETON_JOURS = 60
MAX_CHAPITRES = 80
MAX_ELEMENTS = 80
NS_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

# Début de ligne qui ouvre un chapitre (après nettoyage des espaces)
MOTS_PARTIE = r'(chapitre|chap\.?|partie|s[ée]ance|le[çc]on|module|th[èe]me|semaine|unit[ée]|section|cours)'
RE_PARTIE = re.compile(rf'^{MOTS_PARTIE}\s*[\dIVXLC]+\b|^[IVXLC]{{1,6}}\s*[\.\)\-–:]\s*\S|^\d{{1,2}}\s*[\.\)\-–:]\s+\S',
                       re.IGNORECASE)
# « Chapitre 2 », « Partie II », « III. » : un chapitre même quand le document a aussi des titres Word
RE_CHAPITRE_FORT = re.compile(r'^(chapitre|chap\.?|partie|module|th[èe]me|unit[ée]|le[çc]on)\s*[\dIVXLC]+\b'
                              r'|^[IVXLC]{1,6}\s*[\.\)\-–:]\s*\S', re.IGNORECASE)
RE_SOUS_PARTIE = re.compile(r'^\d{1,2}\s*\.\s*\d')                  # « 1.1 », « 2.3 » : contenu du chapitre
RE_HEURES = re.compile(r'(\d{1,3}(?:[.,]\d)?)\s*(?:h\b|heures?\b|hrs?\b)', re.IGNORECASE)
RE_STYLE_TITRE = re.compile(r'(heading|titre|title)\s*([1-4])', re.IGNORECASE)
RE_PUCE = re.compile(r'^[•▪●◦‣⁃∙\-–—*•·o]\s+')
RE_LIGNE_HEURES = re.compile(r'volume|dur[ée]e|heure', re.IGNORECASE)


# ------------------------------------------------------------------ lecture du document Word

def _texte(element):
    """Texte d'un paragraphe ou d'une cellule (tabulations et retours à la ligne gardés)."""
    morceaux = []
    for n in element.iter():
        if n.tag == NS_W + 't':
            morceaux.append(n.text or '')
        elif n.tag in (NS_W + 'tab',):
            morceaux.append(' ')
        elif n.tag in (NS_W + 'br', NS_W + 'cr'):
            morceaux.append('\n')
        elif n.tag == NS_W + 'p' and morceaux and morceaux[-1] != '\n':
            morceaux.append('\n')
    return re.sub(r'[ \t\xa0]+', ' ', ''.join(morceaux)).strip()


def _style(paragraphe):
    p_pr = paragraphe.find(NS_W + 'pPr')
    style = p_pr.find(NS_W + 'pStyle') if p_pr is not None else None
    return (style.get(NS_W + 'val') or '') if style is not None else ''


def _liste(paragraphe):
    """Vrai pour un paragraphe à puces ou numéroté par Word (numPr)."""
    p_pr = paragraphe.find(NS_W + 'pPr')
    return p_pr is not None and p_pr.find(NS_W + 'numPr') is not None


def heures_de(texte):
    """« Chapitre 2 : Les réseaux (6 h) » → Decimal('6') ; None sinon."""
    m = RE_HEURES.search(texte or '')
    if not m:
        return None
    try:
        h = Decimal(m.group(1).replace(',', '.'))
    except InvalidOperation:
        return None
    return h if 0 < h <= 300 else None


def _heures_cellule(texte):
    t = (texte or '').strip().replace(',', '.')
    if re.fullmatch(r'\d{1,3}(\.\d)?', t):
        h = Decimal(t)
        return h if 0 < h <= 300 else None
    return heures_de(t)


def nettoyer_element(texte):
    return RE_PUCE.sub('', ' '.join((texte or '').split()))[:500]


def _lignes(texte):
    return [x for x in (nettoyer_element(l) for l in (texte or '').split('\n')) if x]


def _chapitres_tableau(tableau):
    """Tableau « Semaine | Contenu | Heures » : un chapitre par ligne (si un en-tête de contenu est reconnu)."""
    lignes = [[_texte(c) for c in tr.findall(NS_W + 'tc')] for tr in tableau.findall(NS_W + 'tr')]
    lignes = [l for l in lignes if any(l)]
    if len(lignes) < 2:
        return None
    entete = [re.sub(r'[^a-z]', '', c.lower().replace('é', 'e').replace('è', 'e').replace('ç', 'c')) for c in lignes[0]]
    i_contenu = next((i for i, c in enumerate(entete) if re.search(r'contenu|theme|chapitre|partie|titre|sujet|lecon'
                                                                   r'|seance|intitule|programme', c)), None)
    if i_contenu is None:
        return None
    i_heures = next((i for i, c in enumerate(entete) if re.search(r'heure|volume|duree|vh|nbh', c)), None)
    i_repere = next((i for i, c in enumerate(entete) if i != i_contenu and re.search(r'semaine|seance|numero|^n$|^no$',
                                                                                     c)), None)
    # Autres colonnes de texte (« Objectifs », « Activités »…) : des contenus du chapitre
    autres = [i for i, c in enumerate(entete) if i not in (i_contenu, i_heures, i_repere)]
    chapitres = []
    for l in lignes[1:]:
        brut = l[i_contenu] if i_contenu < len(l) else ''
        if not brut:
            continue
        titre, _, reste = brut.partition('\n')
        repere = l[i_repere] if i_repere is not None and i_repere < len(l) else ''
        if repere and not re.match(MOTS_PARTIE, titre, re.IGNORECASE):
            titre = f"{repere} · {titre}" if re.search(r'\d', repere) else titre
        heures = _heures_cellule(l[i_heures]) if i_heures is not None and i_heures < len(l) else heures_de(brut)
        elements = _lignes(reste)
        for i in autres:
            if i < len(l):
                elements += _lignes(l[i])
        chapitres.append({'titre': titre.strip()[:255], 'heures': heures, 'elements': elements})
    return chapitres


def _blocs(corps):
    """Le document en blocs : ('titre', niveau, texte), ('chapitre', texte), ('numero', texte), ('sous', texte),
    ('ligne', texte), ('tableau', chapitres)."""
    blocs = []
    for element in corps:
        if element.tag == NS_W + 'tbl':
            chapitres = _chapitres_tableau(element)
            if chapitres:
                blocs.append(('tableau', chapitres))
            else:                                              # tableau sans en-tête reconnu : des lignes de contenu
                blocs.extend(('ligne', x) for x in _lignes(_texte(element)))
            continue
        if element.tag != NS_W + 'p':
            continue
        texte = _texte(element)
        if not texte:
            continue
        m = RE_STYLE_TITRE.search(_style(element))
        if m:                                                  # « Titre » sans numéro : titre du document, ignoré
            blocs.append(('titre', int(m.group(2)), texte.split('\n')[0]))
        elif len(texte) <= 160 and RE_SOUS_PARTIE.match(texte):
            blocs.append(('sous', texte))
        elif len(texte) <= 160 and RE_CHAPITRE_FORT.match(texte) and not _liste(element):
            blocs.append(('chapitre', texte.split('\n')[0]))
        elif len(texte) <= 160 and RE_PARTIE.match(texte) and not _liste(element):
            blocs.append(('numero', texte.split('\n')[0]))
        else:
            blocs.extend(('ligne', x) for x in _lignes(texte))
    return blocs


def decouper_docx(fichier):
    """
    Document Word (.docx) → [{'titre', 'heures', 'elements': [texte, …]}] dans l'ordre du document. Lève ValueError
    si le fichier n'est pas un .docx lisible. Liste vide si aucun chapitre n'est reconnu (l'enseignant les saisit).

    Chapitres : les titres du plus haut niveau utilisé (« Titre 1 », ou « Titre 2 » si un seul « Titre 1 » sert de
    titre au document), et les lignes « Chapitre 2 », « Partie II », « III. » ; sans titres Word, aussi « 2) »,
    « Séance 3 »… Contenus : les sous-titres et lignes « 1.1 » du chapitre ; s'il n'y en a pas, ses lignes
    (paragraphes, puces).
    """
    import xml.etree.ElementTree as ET
    try:
        fichier.seek(0)
        with zipfile.ZipFile(fichier) as z:
            xml = z.read('word/document.xml')
    except (zipfile.BadZipFile, KeyError, OSError):
        raise ValueError("Ce fichier n'est pas un document Word (.docx) lisible. Un ancien .doc : ouvrez-le dans Word "
                         "et « Enregistrer sous » au format .docx.")
    corps = ET.fromstring(xml).find(NS_W + 'body')
    if corps is None:
        return []
    blocs = _blocs(corps)
    niveaux = sorted({b[1] for b in blocs if b[0] == 'titre'})
    niveau = niveaux[0] if niveaux else None
    if niveau is not None and len(niveaux) > 1:
        nb = sum(1 for b in blocs if b[0] == 'titre' and b[1] == niveau)
        if nb == 1 and sum(1 for b in blocs if b[0] == 'titre' and b[1] == niveaux[1]) >= 2:
            niveau = niveaux[1]                                # le seul « Titre 1 » est le titre du document
    chapitres, courant = [], None

    def ouvrir(titre):
        c = {'titre': titre[:255], 'heures': heures_de(titre), 'elements': [], '_sous': [], '_lignes': []}
        chapitres.append(c)
        return c

    for b in blocs:
        genre = b[0]
        if genre == 'tableau':
            chapitres.extend({**c, '_sous': [], '_lignes': c['elements']} for c in b[1])
            courant = None
        elif (genre == 'titre' and b[1] == niveau or genre == 'chapitre'
              or genre == 'numero' and niveau is None):
            courant = ouvrir(b[-1])
        elif courant is None:
            continue                                           # avant le premier chapitre : en-tête, objectifs…
        elif genre == 'titre' and b[1] < niveau:
            courant = None                                     # titre du document entre deux chapitres
        elif genre in ('titre', 'sous', 'numero'):
            courant['_sous'].append(nettoyer_element(b[-1]))
        else:
            texte = b[1]
            if courant['heures'] is None and len(texte) <= 60 and RE_LIGNE_HEURES.search(texte) and heures_de(texte):
                courant['heures'] = heures_de(texte)           # « Volume horaire : 6 h » : pas un contenu
                continue
            courant['_lignes'].append(texte)
    for c in chapitres:
        c['elements'] = [e for e in (c.pop('_sous') or c.pop('_lignes', None) or c['elements']) if e][:MAX_ELEMENTS]
        c.pop('_lignes', None)
    # Un document dont un seul chapitre a été reconnu, avec beaucoup de contenus : pas un vrai découpage
    if len(chapitres) == 1 and len(chapitres[0]['elements']) > 40:
        return []
    return chapitres[:MAX_CHAPITRES]


def enregistrer_chapitres(plan, chapitres):
    """Remplace les chapitres et contenus du plan ; un chapitre sans contenu en reçoit un (son titre) à cocher."""
    from .models import ElementPlan, PartiePlan
    plan.parties.all().delete()
    for k, c in enumerate(chapitres):
        p = PartiePlan.objects.create(plan=plan, ordre=k, titre=c['titre'] or f"Chapitre {k + 1}",
                                      heures_prevues=c.get('heures'))
        elements = c.get('elements') or [p.titre]
        ElementPlan.objects.bulk_create([ElementPlan(partie=p, ordre=i, texte=e[:500])
                                         for i, e in enumerate(elements[:MAX_ELEMENTS])])


# ------------------------------------------------------------------ délégués et liens

def delegues_de(matiere):
    """Délégués de la classe de la matière (programme, niveau, session)."""
    from .models import DelegueClasse
    return list(DelegueClasse.objects.filter(filiere_id=matiere.filiere_id, licence_id=matiere.licence_id,
                                             session_id=matiere.session_id).select_related('etudiant'))


def est_delegue(etudiant, matiere):
    from .models import DelegueClasse
    return DelegueClasse.objects.filter(etudiant=etudiant, filiere_id=matiere.filiere_id,
                                        licence_id=matiere.licence_id, session_id=matiere.session_id).exists()


def jeton(seance, etudiant):
    return signing.dumps({'s': seance.pk, 'e': etudiant.pk}, salt=SEL_LIEN, compress=True)


def lire_jeton(valeur):
    """(id du cours dispensé, id de l'étudiant) ou None (lien faux ou trop ancien)."""
    try:
        d = signing.loads(valeur, salt=SEL_LIEN, max_age=VALIDITE_JETON_JOURS * 86400)
        return int(d['s']), int(d['e'])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None


def horaire_prevu(seance):
    """(arrivée, départ) du cours de l'emploi du temps ce jour-là (même matière, même enseignant), sinon (None, None)."""
    from .models import Seance
    if not seance.date or seance.date.isoweekday() > 6:
        return None, None
    qs = list(Seance.objects.filter(matiere_id=seance.matiere_id, enseignants=seance.enseignant_id,
                                    jour=seance.date.isoweekday()).order_by('debut'))
    if not qs:
        return None, None
    s = next((x for x in qs if x.type == seance.type), qs[0])
    return time(s.debut // 60, s.debut % 60), time(s.fin // 60, s.fin % 60)


def envoyer_liens(request, seance):
    """
    Envoie à chaque délégué de la classe le lien de validation du cours ; le délai (72 h) repart de cet envoi.
    Renvoie les adresses utilisées.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse
    from django.utils import timezone
    from .comptes import _adresse_site
    site = _adresse_site(request)
    maintenant = timezone.now()
    limite = maintenant + timedelta(hours=DELAI_DELEGUE_HEURES)
    envoyes = []
    reajuste = seance.statut == seance.ATTENTE_DELEGUE_REAJ
    for d in delegues_de(seance.matiere):
        e = d.etudiant
        if not (e.email or '').strip() or '@' not in e.email:
            continue
        contexte = {'etudiant': e, 'seance': seance, 'matiere': seance.matiere, 'enseignant': seance.enseignant,
                    'elements': list(seance.elements.select_related('partie')), 'limite': limite,
                    'reajuste': reajuste, 'delai': f"{DELAI_DELEGUE_HEURES} heures",
                    'lien': f"{site}{reverse('plan_confirmer', args=[jeton(seance, e)])}",
                    'logo': f"{site}{settings.STATIC_URL}pfe/img/logo_ist.png"}
        sujet = (f"GePed : heures réajustées à signer · {seance.matiere.nom} du {seance.date:%d/%m/%Y}" if reajuste
                 else f"GePed : validez le cours de {seance.matiere.nom} du {seance.date:%d/%m/%Y}")
        message = EmailMultiAlternatives(sujet, render_to_string('pfe/emails/plan_confirmer.txt', contexte),
                                         settings.DEFAULT_FROM_EMAIL, [e.email])
        message.attach_alternative(render_to_string('pfe/emails/plan_confirmer.html', contexte), 'text/html')
        message.send()
        envoyes.append(e.email)
    if envoyes:
        seance.lien_envoye_le, seance.expire_le = maintenant, limite
        seance.save(update_fields=['lien_envoye_le', 'expire_le'])
    return envoyes


def enregistrer_signature(objet, champ, png, qui):
    """Signature (PNG déjà préparé) dans le stockage privé ; `champ` : signature_enseignant ou signature_delegue."""
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from .models import stockage_prive
    stockage = stockage_prive()
    ancien = getattr(objet, champ)
    if ancien:
        stockage.delete(ancien.name)
    nom = stockage.save(f"plans_cours/signatures/{timezone.now():%Y}/cours_{objet.pk}_{qui}.png", ContentFile(png))
    setattr(objet, champ, nom)
