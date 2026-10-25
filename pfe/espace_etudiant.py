"""
Espace étudiant (sans compte) : inscription des préinscrits et réinscription des anciens.

Identification :
  - inscription   : INE + PV du bac ; l'étudiant doit être PRÉINSCRIT dans la session ouverte ;
  - réinscription : INE + matricule ; étudiant inscrit, pas encore inscrit dans la session ouverte.
Sécurité (pas de mot de passe) : 5 essais manqués par adresse IP, puis 15 minutes d'attente ;
l'accès au dossier dure 30 minutes ; aucune donnée sensible n'est affichée (ni notes, ni
handicap, ni anciennes pièces).

Ce que l'étudiant propose (contacts, bourse, formation, photo, niveau) reste EN ATTENTE dans une
DemandeEtudiant : rien ne change sur sa fiche avant la validation par la scolarité (appliquer).
"""
import re
import time

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import DemandeEtudiant, Etudiant, Inscription, Session

ESSAIS_MAX = 5
ATTENTE = 15 * 60                     # secondes de blocage après ESSAIS_MAX échecs
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
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', '') or 'inconnue')


def bloque(request):
    """Secondes restantes de blocage (0 si l'adresse peut encore essayer)."""
    d = cache.get(f'espace:{_ip(request)}')
    if d and d['n'] >= ESSAIS_MAX:
        return max(0, int(d['depuis'] + ATTENTE - time.time()))
    return 0


def echec(request):
    cle = f'espace:{_ip(request)}'
    d = cache.get(cle) or {'n': 0, 'depuis': time.time()}
    d['n'] += 1
    d['depuis'] = time.time()
    cache.set(cle, d, ATTENTE)
    return ESSAIS_MAX - d['n']


def reussite(request):
    cache.delete(f'espace:{_ip(request)}')


# ------------------------------------------------------------------ identification

class Refus(Exception):
    """Identification refusée : message affiché à l'étudiant (sans dire quel champ est faux)."""


INCONNU = "Aucun dossier ne correspond à ces informations. Vérifiez-les ou adressez-vous à la scolarité."


def identifier_inscription(ine, pvbac, session):
    """Préinscrit de la session : (étudiant, inscription)."""
    ine, pvbac = _norm(ine), _norm(pvbac)
    if not ine or not pvbac:
        raise Refus("Saisissez votre INE et votre numéro de PV du bac.")
    etudiants = [e for e in Etudiant.objects.filter(ine__iexact=ine) if _norm(e.pvbac) == pvbac]
    if len(etudiants) != 1:
        raise Refus(INCONNU)
    e = etudiants[0]
    insc = e.etudiant_inscription.filter(session=session).select_related('filiere', 'licence', 'promotion').first()
    if not e.preinscrit or insc is None:
        if not e.preinscrit and insc is not None:
            raise Refus(f"Votre inscription pour {session} est déjà validée : adressez-vous à la scolarité "
                        f"pour toute modification.")
        raise Refus(f"Vous n'êtes pas préinscrit(e) pour {session}. Ancien étudiant ? Utilisez l'onglet "
                    f"« Réinscription ».")
    return e, insc


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
    if e.etudiant_inscription.filter(session=session).exists():
        raise Refus(f"Vous êtes déjà inscrit(e) pour {session} : adressez-vous à la scolarité pour toute "
                    f"modification.")
    derniere = (e.etudiant_inscription.select_related('session', 'licence', 'filiere', 'promotion')
                .order_by('-session__annee', '-licence__nom').first())
    if derniere is None:
        raise Refus(INCONNU)
    return e, derniere


def ouvrir_acces(request, etudiant, type_demande, inscription):
    request.session[CLE_SESSION] = {'etudiant': etudiant.pk, 'type': type_demande, 'inscription': inscription.pk,
                                    'expire': time.time() + DUREE_ACCES}
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


def niveaux_proposes(derniere):
    """Niveaux proposés à la réinscription : ceux de la même famille (LICENCE n, n ème Année…),
    du même rang ou au-dessus ; le niveau suivant est proposé par défaut."""
    from .models import Licence
    nom = (derniere.licence.nom or '').upper()
    famille = 'ANNEE' if 'ANN' in nom else re.sub(r'[\s\d]+', '', nom)

    def meme_famille(l):
        n = (l.nom or '').upper()
        return ('ANN' in n) if famille == 'ANNEE' else re.sub(r'[\s\d]+', '', n) == famille
    rang = _rang(derniere.licence)
    niveaux = sorted((l for l in Licence.objects.all() if meme_famille(l) and _rang(l) >= rang), key=_rang)
    suivant = next((l for l in niveaux if _rang(l) == rang + 1), derniere.licence)
    return niveaux or [derniere.licence], suivant


# ------------------------------------------------------------------ validation par la scolarité

def appliquer(demande, utilisateur):
    """
    Applique la demande : contacts (valeurs renseignées), bourse, formation et photo proposés ;
    pour une réinscription, crée l'inscription de la session au niveau demandé (même programme,
    même promotion, même cycle). Pour une inscription, l'étudiant n'est plus préinscrit.
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
            d = demande.derniere_inscription
            licence = demande.licence or (d.licence if d else None)
            if d and licence and not Inscription.objects.filter(etudiant=e, session=demande.session,
                                                                 licence=licence).exists():
                Inscription.objects.create(etudiant=e, session=demande.session, licence=licence, filiere=d.filiere,
                                           promotion=d.promotion, cycle=d.cycle, redouble=(licence.pk == d.licence_id))
        demande.statut = DemandeEtudiant.VALIDEE
        demande.traitee_le, demande.traitee_par = timezone.now(), utilisateur
        demande.motif_refus = ''
        demande.save()
    return demande


def refuser(demande, utilisateur, motif):
    from .models import stockage_prive
    if demande.photo:
        stockage_prive().delete(demande.photo.name)
        demande.photo = ''
    demande.statut = DemandeEtudiant.REFUSEE
    demande.traitee_le, demande.traitee_par, demande.motif_refus = timezone.now(), utilisateur, motif
    demande.save()
    return demande
