"""
Espace étudiant (sans compte) : inscription des préinscrits et réinscription des anciens.

Identification :
  - inscription   : INE + PV du bac ; l'étudiant doit être PRÉINSCRIT dans la session ouverte ;
  - réinscription : INE + matricule ; étudiant inscrit, pas encore inscrit dans la session ouverte ;
  - Master (inscription ou réinscription) : adresse e-mail de la fiche, puis lien personnel envoyé
    par e-mail (valable 1 heure, utilisable une fois). Les Masters ne passent pas par l'INE.
Seules les promotions prévues pour la session (Promotion_Licence_Session) passent en ligne ; les
autres étudiants doivent se présenter à la scolarité. À la réinscription, le niveau proposé est celui
prévu pour la promotion dans la session.
Sécurité (pas de mot de passe) :
  - 5 essais manqués pour un même INE, ou 10 depuis un même appareil (adresse IP), puis 15 minutes
    d'attente : deviner un PV du bac ou un matricule est impossible en pratique ;
  - l'accès au dossier dure 30 minutes (nouvel identifiant de session à l'identification) ;
  - aucune donnée sensible n'est affichée (ni notes, ni handicap, ni anciennes pièces) ;
  - échecs journalisés (adresse IP et INE, jamais le PV ni le matricule) ;
  - en-têtes de sécurité stricts sur toutes les pages de l'espace (voir views_espace.py).

Ce que l'étudiant propose (contacts, bourse, formation, photo, niveau) reste EN ATTENTE dans une
DemandeEtudiant : rien ne change sur sa fiche avant la validation par la scolarité (appliquer).
"""
import re
import time

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import DemandeEtudiant, Etudiant, Inscription, Promotion_Licence_Session, Session

ESSAIS_MAX = 5                        # échecs pour un même INE
ESSAIS_MAX_APPAREIL = 10              # échecs depuis une même adresse (plusieurs étudiants derrière un même Wi-Fi)
ATTENTE = 15 * 60                     # secondes de blocage après ces échecs
DUREE_ACCES = 30 * 60                 # secondes d'accès au dossier après identification
CLE_SESSION = 'espace_etudiant'


def session_cible():
    """Session des dépôts : la session en cours, sinon la plus récente."""
    return (Session.objects.filter(en_cours=True).order_by('-annee').first()
            or Session.objects.order_by('-annee').first())


def _norm(v):
    return re.sub(r'\s+', '', str(v or '')).upper()


# ------------------------------------------------------------------ essais limités

def _ip(request):
    """
    Adresse de l'appareil. La PREMIÈRE valeur de X-Forwarded-For est choisie par le client (falsifiable) :
    on prend l'adresse vue par le serveur, et la DERNIÈRE valeur ajoutée par le relais s'il y en a un.
    """
    relais = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[-1].strip()
    return f"{request.META.get('REMOTE_ADDR', '') or 'inconnue'}|{relais[:45]}"


def _cles(request, ine):
    import hashlib
    cles = [('appareil', hashlib.sha256(_ip(request).encode()).hexdigest()[:32], ESSAIS_MAX_APPAREIL)]
    if ine:
        cles.append(('ine', hashlib.sha256(_norm(ine).encode()).hexdigest()[:32], ESSAIS_MAX))
    return cles


def bloque(request, ine=''):
    """Secondes restantes de blocage (0 si l'appareil, et cet INE, peuvent encore essayer)."""
    reste = 0
    for genre, cle, maxi in _cles(request, ine):
        d = cache.get(f'espace:{genre}:{cle}')
        if d and d['n'] >= maxi:
            reste = max(reste, int(d['depuis'] + ATTENTE - time.time()))
    return max(0, reste)


def echec(request, ine='', journal=True):
    """Compte l'échec (appareil et INE) ; renvoie les essais restants pour cet INE."""
    import logging
    if journal:
        logging.getLogger('pfe').warning("Espace étudiant : identification refusée (adresse %s, INE %s)",
                                         request.META.get('REMOTE_ADDR', '?'),
                                         'adresse e-mail' if '@' in str(ine) else (_norm(ine)[:25] or '—'))
    restants = ESSAIS_MAX
    for genre, cle, maxi in _cles(request, ine):
        cle_cache = f'espace:{genre}:{cle}'
        d = cache.get(cle_cache) or {'n': 0, 'depuis': time.time()}
        d['n'] += 1
        d['depuis'] = time.time()
        cache.set(cle_cache, d, ATTENTE)
        restants = min(restants, maxi - d['n'])
    return restants


def reussite(request, ine=''):
    for genre, cle, _ in _cles(request, ine):
        if genre == 'ine':                    # l'appareil garde ses échecs (autres INE essayés)
            cache.delete(f'espace:{genre}:{cle}')


# ------------------------------------------------------------------ identification

class Refus(Exception):
    """Identification refusée : message affiché à l'étudiant (sans dire quel champ est faux)."""


INCONNU = "Aucun dossier ne correspond à ces informations. Vérifiez-les ou adressez-vous à la scolarité."
HORS_PROMOTION = ("Votre promotion n'est pas prévue pour les inscriptions en ligne de {session} : "
                  "présentez-vous au Service de la Scolarité.")


VERS_MASTER = ("Étudiant(e) en Master : utilisez l'onglet « Master » avec votre adresse e-mail ; vous recevrez "
               "un lien personnel.")


def est_master(inscription):
    """Inscription en Master : cycle, niveau M1 / M2 / MASTER…, ou programme de Master (voir signataires.py)."""
    from .signataires import est_master as master
    return master(inscription.cycle, inscription.licence, inscription.filiere)


def _inscription_session(e, session):
    return e.etudiant_inscription.filter(session=session).select_related('filiere', 'licence', 'promotion').first()


def identifier_inscription(ine, pvbac, session):
    """Préinscrit de la session : (étudiant, inscription). Les Masters passent par l'onglet Master."""
    ine, pvbac = _norm(ine), _norm(pvbac)
    if not ine or not pvbac:
        raise Refus("Saisissez votre INE et votre numéro de PV du bac.")
    etudiants = [e for e in Etudiant.objects.filter(ine__iexact=ine) if _norm(e.pvbac) == pvbac]
    if len(etudiants) != 1:
        raise Refus(INCONNU)
    e = etudiants[0]
    insc = _inscription_session(e, session)
    if e.preinscrit and insc is not None and est_master(insc):
        raise Refus(VERS_MASTER)
    return e, _verifier_preinscrit(e, insc, session)


def _verifier_preinscrit(e, insc, session):
    """Préinscrit de la session, d'une promotion prévue en ligne : son inscription, sinon Refus."""
    if not e.preinscrit or insc is None:
        if not e.preinscrit and insc is not None:
            raise Refus(f"Votre inscription pour {session} est déjà validée : adressez-vous à la scolarité "
                        f"pour toute modification.")
        raise Refus(f"Vous n'êtes pas préinscrit(e) pour {session}. Ancien étudiant ? Utilisez l'onglet "
                    f"« Réinscription ».")
    if not insc.promotion_id or not Promotion_Licence_Session.objects.filter(
            promotion_id=insc.promotion_id, licence_id=insc.licence_id, session=session).exists():
        raise Refus(HORS_PROMOTION.format(session=session))
    return insc


def identifier_reinscription(ine, matricule, session):
    """Ancien étudiant pas encore inscrit dans la session : (étudiant, dernière inscription)."""
    ine, matricule = _norm(ine), _norm(matricule)
    if not ine or not matricule:
        raise Refus("Saisissez votre INE et votre matricule.")
    etudiants = [e for e in Etudiant.objects.filter(ine__iexact=ine) if _norm(e.matricule) == matricule]
    if len(etudiants) != 1:
        raise Refus(INCONNU)
    e = etudiants[0]
    if e.preinscrit:
        raise Refus("Vous êtes préinscrit(e) : utilisez l'onglet « Inscription » (INE et PV du bac).")
    derniere = _derniere_inscription(e)
    if derniere is not None and reinscription_master(derniere, session):
        raise Refus(VERS_MASTER)
    return e, _verifier_ancien(e, session)


def _derniere_inscription(e):
    return (e.etudiant_inscription.select_related('session', 'licence', 'filiere', 'promotion')
            .order_by('-session__annee', '-licence__nom').first())


def reinscription_master(derniere, session):
    """Réinscription en Master : la dernière inscription est en Master, ou le niveau prévu en est un."""
    from .signataires import est_master as master
    return est_master(derniere) or any(master(licence=n) for n in niveaux_prevus(derniere, session))


def _verifier_ancien(e, session):
    """Ancien étudiant pas encore inscrit dans la session, d'une promotion prévue : sa dernière inscription."""
    if e.etudiant_inscription.filter(session=session).exists():
        raise Refus(f"Vous êtes déjà inscrit(e) pour {session} : adressez-vous à la scolarité pour toute "
                    f"modification.")
    derniere = _derniere_inscription(e)
    if derniere is None:
        raise Refus(INCONNU)
    if not derniere.promotion_id or not niveaux_prevus(derniere, session):
        raise Refus(HORS_PROMOTION.format(session=session))
    return derniere


# ------------------------------------------------------------------ Master : adresse e-mail et lien personnel

DUREE_LIEN = 60 * 60                  # secondes de validité du lien envoyé par e-mail
SEL_LIEN = 'gped.espace.master'


class RefusConnu(Refus):
    """Adresse connue mais accès impossible : la raison part par e-mail (jamais affichée à l'écran)."""
    def __init__(self, message, etudiant):
        super().__init__(message)
        self.etudiant = etudiant


def identifier_master(email, session, ouvert=None):
    """
    Étudiant en Master d'après l'adresse e-mail de sa fiche : (étudiant, type de demande, inscription).
    Préinscrit en Master dans la session → inscription ; ancien étudiant de Master → réinscription.
    `ouvert(type_demande)` : les inscriptions / réinscriptions en ligne sont-elles ouvertes ?
    Adresse inconnue : Refus (rien n'est envoyé) ; adresse connue : RefusConnu (raison envoyée par e-mail).
    """
    email = (email or '').strip()
    e = Etudiant.objects.filter(email__iexact=email).first() if email else None
    if e is None:
        raise Refus(INCONNU)
    try:
        if e.preinscrit:
            insc = _inscription_session(e, session)
            if insc is None or not est_master(insc):
                raise Refus(f"Vous n'êtes pas préinscrit(e) en Master pour {session} : utilisez l'onglet "
                            f"« Inscription » (INE et PV du bac).")
            type_demande, inscription = DemandeEtudiant.INSCRIPTION, _verifier_preinscrit(e, insc, session)
        else:
            derniere = _derniere_inscription(e)
            if derniere is None or not (est_master(derniere) or reinscription_master(derniere, session)):
                raise Refus("Vous n'êtes pas étudiant(e) en Master : utilisez l'onglet « Réinscription » "
                            "(INE et matricule).")
            type_demande, inscription = DemandeEtudiant.REINSCRIPTION, _verifier_ancien(e, session)
        if ouvert is not None and not ouvert(type_demande):
            quoi = 'inscriptions' if type_demande == DemandeEtudiant.INSCRIPTION else 'réinscriptions'
            raise Refus(f"Les {quoi} en ligne de {session} sont fermées : renseignez-vous auprès de la scolarité.")
    except RefusConnu:
        raise
    except Refus as r:
        raise RefusConnu(str(r), e)
    return e, type_demande, inscription


def creer_lien(etudiant):
    """Jeton signé du lien personnel : valable DUREE_LIEN, utilisable une seule fois (marque gardée en cache)."""
    import secrets
    from django.core import signing
    nonce = secrets.token_urlsafe(16)
    cache.set(f'espace:lien:{nonce}', etudiant.pk, DUREE_LIEN)
    return signing.dumps({'e': etudiant.pk, 'n': nonce}, salt=SEL_LIEN)


def lire_lien(jeton, consommer=False):
    """Étudiant du lien (None s'il est faux, expiré ou déjà utilisé) ; `consommer` : le lien ne servira plus."""
    from django.core import signing
    try:
        d = signing.loads(jeton, salt=SEL_LIEN, max_age=DUREE_LIEN)
    except signing.BadSignature:
        return None
    cle = f"espace:lien:{d.get('n')}"
    if cache.get(cle) != d.get('e'):
        return None
    if consommer:
        cache.delete(cle)
    return Etudiant.objects.filter(pk=d['e']).first()


def ouvrir_acces(request, etudiant, type_demande, inscription, master=False):
    request.session[CLE_SESSION] = {'etudiant': etudiant.pk, 'type': type_demande, 'inscription': inscription.pk,
                                    'master': bool(master), 'expire': time.time() + DUREE_ACCES}
    request.session.cycle_key()                   # nouvel identifiant de session après identification


def acces(request):
    """Accès en cours (dict) ou None s'il n'y en a pas ou s'il a expiré."""
    a = request.session.get(CLE_SESSION)
    if not a or a.get('expire', 0) < time.time():
        request.session.pop(CLE_SESSION, None)
        return None
    return a


def fermer_acces(request):
    request.session.pop(CLE_SESSION, None)


# ------------------------------------------------------------------ niveaux

def _rang(licence):
    """« LICENCE 3 » → 3 ; « 2ème Année » → 2 ; « M1 » → 1."""
    m = re.search(r'\d+', licence.nom or '')
    return int(m.group()) if m else 0


def _famille(licence):
    """« LICENCE 3 » → « LICENCE » ; « 2ème Année » → « ANNEE » (pour ne pas mélanger les deux écritures)."""
    nom = (licence.nom or '').upper()
    return 'ANNEE' if 'ANN' in nom else re.sub(r'[\s\d]+', '', nom)


def niveaux_prevus(derniere, session):
    """Niveaux prévus pour la promotion de l'étudiant dans la session (Promotion_Licence_Session),
    de la même écriture que son dernier niveau quand c'est possible."""
    from .models import Licence
    prevus = list(Licence.objects.filter(licence_promotion_session__promotion_id=derniere.promotion_id,
                                         licence_promotion_session__session=session).distinct())
    memes = [l for l in prevus if _famille(l) == _famille(derniere.licence)]
    return sorted(memes or prevus, key=_rang)


def niveaux_proposes(derniere, session):
    """Niveaux proposés à la réinscription (prévus pour la promotion) ; le premier est sélectionné."""
    niveaux = niveaux_prevus(derniere, session)
    return niveaux, (niveaux[0] if niveaux else None)


# ------------------------------------------------------------------ validation par la scolarité

def prevision_reinscription(demande):
    """
    Inscription préremplie pour la décision : niveau prévu pour la promotion (Promotion_Licence_Session),
    promotion, programme et cycle de la dernière inscription. L'agent peut tout changer.
    """
    d = demande.derniere_inscription
    niveaux = niveaux_prevus(d, demande.session) if d else []
    licence = demande.licence or (niveaux[0] if niveaux else (d.licence if d else None))
    return {'licence': licence, 'promotion': d.promotion if d else None, 'filiere': d.filiere if d else None,
            'cycle': d.cycle if d else None, 'redouble': bool(d and licence and licence.pk == d.licence_id)}


def appliquer(demande, utilisateur, inscription=None):
    """
    Applique la demande : contacts (valeurs renseignées), bourse, formation et photo proposés ;
    pour une réinscription, crée l'inscription de la session : celle choisie par l'agent
    (`inscription` : licence, promotion, filiere, cycle, redouble), sinon la prévision.
    Pour une inscription, l'étudiant n'est plus préinscrit.
    """
    from .photos import enregistrer_photo
    e = demande.etudiant
    with transaction.atomic():
        champs = []
        for champ in DemandeEtudiant.CHAMPS_CONTACTS:
            valeur = (demande.contacts or {}).get(champ)
            if not valeur or getattr(e, champ) == valeur:
                continue
            if champ == 'email' and Etudiant.objects.filter(email__iexact=valeur).exclude(pk=e.pk).exists():
                continue                          # e-mail déjà pris : on garde l'ancien
            setattr(e, champ, valeur)
            champs.append(champ)
        for champ in ('boursier', 'type_formation'):
            if getattr(demande, champ) and getattr(demande, champ) != getattr(e, champ):
                setattr(e, champ, getattr(demande, champ))
                champs.append(champ)
        if demande.type == DemandeEtudiant.INSCRIPTION:
            e.preinscrit = False
            champs.append('preinscrit')
            if not e.boursier:
                e.boursier = 'boursier'
                champs.append('boursier')
            if not e.type_formation:
                e.type_formation = 'initiale'
                champs.append('type_formation')
        if champs:
            e.save(update_fields=sorted(set(champs)))
        if demande.photo:
            from .models import stockage_prive
            stockage = stockage_prive()
            with stockage.open(demande.photo.name, 'rb') as f:
                enregistrer_photo(e, f.read(), e.matricule or f"etudiant-{e.pk}")
            stockage.delete(demande.photo.name)
            demande.photo = ''
        if demande.type == DemandeEtudiant.REINSCRIPTION:
            choix = {**prevision_reinscription(demande), **(inscription or {})}
            licence = choix['licence']
            if licence:
                demande.licence = licence
            if licence and not Inscription.objects.filter(etudiant=e, session=demande.session,
                                                          licence=licence).exists():
                Inscription.objects.create(etudiant=e, session=demande.session, licence=licence,
                                           filiere=choix['filiere'], promotion=choix['promotion'],
                                           cycle=choix['cycle'] or None, redouble=bool(choix['redouble']))
        demande.statut = DemandeEtudiant.VALIDEE
        demande.traitee_le, demande.traitee_par = timezone.now(), utilisateur
        demande.motif_refus = ''
        demande.save()
    return demande


def cloturer_traitees_ailleurs(session=None):
    """
    Demandes en attente déjà réglées par un autre moyen : elles quittent « Demandes en ligne ».
      - inscription : l'étudiant n'est plus préinscrit (bouton « Inscrire », case décochée, import…) ;
      - réinscription : l'étudiant a déjà une inscription dans la session (saisie, import…).
    Elles sont classées « Validées » sans auteur (validées hors des demandes en ligne) ; les
    changements proposés ne sont PAS appliqués, la nouvelle photo proposée est supprimée.
    Renvoie le nombre de demandes classées.
    """
    from .models import stockage_prive
    qs = DemandeEtudiant.objects.filter(statut=DemandeEtudiant.EN_ATTENTE)
    if session is not None:
        qs = qs.filter(session=session)
    demandes = list(qs.select_related('etudiant'))
    reinscrits = set(Inscription.objects.filter(
        etudiant_id__in=[d.etudiant_id for d in demandes if d.type == DemandeEtudiant.REINSCRIPTION],
        session_id__in={d.session_id for d in demandes}).values_list('etudiant_id', 'session_id'))
    a_clore = [d for d in demandes
               if (d.type == DemandeEtudiant.INSCRIPTION and not d.etudiant.preinscrit)
               or (d.type == DemandeEtudiant.REINSCRIPTION and (d.etudiant_id, d.session_id) in reinscrits)]
    for d in a_clore:
        if d.photo:
            stockage_prive().delete(d.photo.name)
            d.photo = ''
        d.statut, d.traitee_le, d.traitee_par, d.motif_refus = DemandeEtudiant.VALIDEE, timezone.now(), None, ''
        d.save(update_fields=['statut', 'traitee_le', 'traitee_par', 'motif_refus', 'photo', 'date_maj'])
    return len(a_clore)


def refuser(demande, utilisateur, motif):
    from .models import stockage_prive
    if demande.photo:
        stockage_prive().delete(demande.photo.name)
        demande.photo = ''
    demande.statut = DemandeEtudiant.REFUSEE
    demande.traitee_le, demande.traitee_par, demande.motif_refus = timezone.now(), utilisateur, motif
    demande.save()
    return demande
