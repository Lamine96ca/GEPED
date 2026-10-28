"""
Signataire des documents pédagogiques (relevés, attestations, fiche d'inscription) et informations
de l'en-tête (service, e-mail, téléphone).

  - Étudiant en Master : le personnel coché « Chef » et rattaché au programme du document ;
  - sinon : le chef du Service Scolarité ;
  - aucun chef enregistré : le réglage GPED_ATTESTATION (signataire, fonction, coordonnées).

Sous la signature : la fonction du chef (fiche Personnel), puis civilité, prénom et NOM.
L'image « signature et cachet » (signature_scolarite.png) est celle de la scolarité : un document
signé par un chef de programme s'imprime avec son nom et un espace pour signer à la main.
"""
from dataclasses import dataclass

from django.conf import settings

MASTER = 'Master'


@dataclass(frozen=True)
class Signataire:
    nom: str                    # « M. Lanciné 2 CONDE »
    fonction: str               # « Le Chef Service de la Scolarité »
    service: str                # sous le bandeau de l'en-tête : « SERVICE SCOLARITÉ » ou le programme
    email: str
    telephone: str
    scolarite: bool = True      # l'image « signature et cachet » de la scolarité peut s'imprimer


def est_master(cycle=None, licence=None, filiere=None):
    """Master : cycle de l'inscription (ou de l'attestation), niveau M1 / M2 / MASTER…, ou programme de Master."""
    if (cycle or '') == MASTER or (filiere is not None and (filiere.cycle or '') == MASTER):
        return True
    nom = (getattr(licence, 'nom', '') or '').strip().upper()
    return nom.startswith('MASTER') or nom in ('M1', 'M2')


def _par_defaut():
    conf = settings.GPED_ATTESTATION
    return Signataire(conf['signataire'], conf['fonction'], 'SERVICE SCOLARITÉ', conf['email'], conf['telephone'])


def chef_scolarite():
    from .models import Personnel
    from .roles import est_service_scolarite
    for p in Personnel.objects.filter(chef=True).prefetch_related('service').order_by('nom', 'prenom'):
        if any(est_service_scolarite(s) for s in p.service.all()):
            return p
    return None


def chef_programme(filiere):
    from .models import Personnel
    return Personnel.objects.filter(chef=True, service=filiere).order_by('nom', 'prenom').first()


def signataire(filiere=None, cycle=None, licence=None, master=None):
    """Signataire d'un document de ce programme / cycle / niveau (`master` : déjà décidé par l'appelant)."""
    defaut = _par_defaut()
    if master is None:
        master = est_master(cycle, licence, filiere)
    if master and filiere is not None:
        chef = chef_programme(filiere)
        if chef is not None:
            return Signataire(chef.nom_signature, chef.fonc or defaut.fonction, filiere.nom.upper(),
                              chef.email or defaut.email, chef.telephone or defaut.telephone, scolarite=False)
    chef = chef_scolarite()
    if chef is not None:
        return Signataire(chef.nom_signature, chef.fonc or defaut.fonction, defaut.service,
                          chef.email or defaut.email, chef.telephone or defaut.telephone)
    return defaut


def signataire_releve(releve, contenu):
    """Relevé ou extrait : Master si l'une des inscriptions imprimées (à défaut, du programme) est en Master."""
    from .models import Inscription
    inscriptions = [b['inscription'] for b in contenu.get('blocs') or []] or list(
        Inscription.objects.filter(etudiant_id=releve.etudiant_id, filiere_id=releve.filiere_id)
        .select_related('licence'))
    master = any(est_master(i.cycle, i.licence, releve.filiere) for i in inscriptions) \
        or est_master(None, releve.licence, releve.filiere)
    return signataire(releve.filiere, master=master)


def signataire_attestation(attestation):
    return signataire(attestation.filiere, attestation.cycle, attestation.licence)


def signataire_inscription(inscription):
    return signataire(inscription.filiere, inscription.cycle, inscription.licence)
