"""
Rôles gePed et périmètre de visibilité des étudiants.

- Scolarité   : superutilisateur, ou personnel rattaché au service de la scolarité
                (code settings.GPED_SCOLARITE_CODE ou nom dans settings.GPED_SCOLARITE_NOMS).
                Peut inscrire, importer, modifier, exporter.
- Département : personnel rattaché à un Service non-programme. Consulte et exporte la liste.
- Programme   : personnel rattaché à un Service programme. Consulte et exporte
                uniquement les étudiants inscrits dans ses programmes.

Matières : la scolarité voit et gère toutes les matières ; un chef de programme voit et gère
uniquement celles de son ou ses programmes ; les autres services les voient toutes, sans
pouvoir les modifier.
"""
from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied

from .models import Inscription, Matiere, Service

SCOLARITE = 'scolarite'
DEPARTEMENT = 'departement'
PROGRAMME = 'programme'
AUCUN = 'aucun'

LIBELLES = {
    SCOLARITE: 'Scolarité',
    DEPARTEMENT: 'Département',
    PROGRAMME: 'Programme',
    AUCUN: 'Aucun accès',
}


class Profil:
    def __init__(self, role, services=(), personnel=None, superuser=False, tous_services=None):
        self.role = role
        self.services = list(services)
        self.personnel = personnel
        self.superuser = superuser
        # Tous les services du personnel (le rôle n'en garde qu'une partie) : sert aux droits de chef
        self.tous_services = list(tous_services if tous_services is not None else services)

    @property
    def est_chef(self):
        return bool(self.personnel and self.personnel.chef)

    @property
    def est_chef_scolarite(self):
        """Super utilisateur, ou chef rattaché au Service Scolarité."""
        return self.superuser or (self.est_chef and any(est_service_scolarite(s) for s in self.tous_services))

    @property
    def departements_diriges(self):
        """Départements dont le profil est chef : programmes qui sont leur propre département."""
        if not self.est_chef:
            return set()
        return {s.id for s in self.tous_services if s.programme and s.departement_id == s.id}

    @property
    def masters_diriges(self):
        """Programmes de Master dont le profil est chef (directeur du Master) : il gère leurs attestations."""
        if not self.est_chef:
            return set()
        return {s.id for s in self.tous_services if s.programme and (s.cycle or '') == 'Master'}

    @property
    def gere_attestations(self):
        """Scolarité (toutes les attestations), ou directeur de Master (celles de ses programmes)."""
        return self.est_scolarite or bool(self.masters_diriges)

    @property
    def gere_etudiants(self):
        """
        Fiches, dossiers, inscriptions, relevés : la scolarité (tous les étudiants), ou le directeur de Master
        (les étudiants de ses programmes de Master). Valider ou invalider une inscription, importer une
        liste et délibérer restent à la scolarité.
        """
        return self.est_scolarite or bool(self.masters_diriges)

    def est_chef_de(self, filiere):
        """Chef pour ce programme : chef de la scolarité, ou chef du département du programme."""
        return self.est_chef_scolarite or bool(filiere and filiere.departement_effectif.id in self.departements_diriges)

    @property
    def libelle(self):
        return LIBELLES[self.role]

    @property
    def est_scolarite(self):
        return self.role == SCOLARITE

    @property
    def a_acces(self):
        return self.role != AUCUN

    @property
    def programme_ids(self):
        return [s.id for s in self.services if s.programme]

    @property
    def gere_matieres(self):
        """Ajouter, modifier, supprimer, importer des matières (dans son périmètre)."""
        return self.role in (SCOLARITE, PROGRAMME)

    @property
    def structure(self):
        """Nom des services affichés à côté du rôle."""
        return ', '.join(s.nom for s in self.services)


def est_service_scolarite(service):
    """
    Service de la scolarité : reconnu par son code (GPED_SCOLARITE_CODE) ou par son nom
    (GPED_SCOLARITE_NOMS, sans tenir compte de la casse), car les codes des services
    de l'établissement ne sont pas toujours renseignés (« 0000 »).
    """
    if service.programme:
        return False
    if (service.code or '').strip().upper() == settings.GPED_SCOLARITE_CODE.upper():
        return True
    noms = {n.strip().lower() for n in getattr(settings, 'GPED_SCOLARITE_NOMS', [])}
    return service.nom.strip().lower() in noms


def _calculer_profil(user):
    if not user.is_authenticated:
        return Profil(AUCUN)
    personnel = getattr(user, 'profil_personnel', None)
    services = list(personnel.service.all()) if personnel else []
    scolarite = [s for s in services if est_service_scolarite(s)]

    options = {'superuser': user.is_superuser, 'tous_services': services}
    if user.is_superuser or scolarite:
        return Profil(SCOLARITE, scolarite, personnel, **options)
    programmes = [s for s in services if s.programme]
    if programmes:
        return Profil(PROGRAMME, programmes, personnel, **options)
    if services:
        return Profil(DEPARTEMENT, services, personnel, **options)
    return Profil(AUCUN, [], personnel, **options)


def get_profil(request):
    """Profil mis en cache sur la requête."""
    if not hasattr(request, '_gped_profil'):
        request._gped_profil = _calculer_profil(request.user)
    return request._gped_profil


def inscriptions_visibles(profil):
    """QuerySet des inscriptions que le profil a le droit de voir."""
    qs = Inscription.objects.all()
    if profil.role in (SCOLARITE, DEPARTEMENT):
        return qs
    if profil.role == PROGRAMME:
        return qs.filter(filiere_id__in=profil.programme_ids)
    return qs.none()


def matieres_visibles(profil):
    """QuerySet des matières que le profil a le droit de voir."""
    qs = Matiere.objects.all()
    if profil.role in (SCOLARITE, DEPARTEMENT):
        return qs
    if profil.role == PROGRAMME:
        return qs.filter(filiere_id__in=profil.programme_ids)
    return qs.none()


def programmes_du_profil(profil):
    """Programmes proposés dans les listes : tous (scolarité, autres services), sinon les siens."""
    qs = Service.objects.filter(programme=True).order_by('nom')
    if profil.role == PROGRAMME:
        return qs.filter(id__in=profil.programme_ids)
    return qs if profil.role in (SCOLARITE, DEPARTEMENT) else qs.none()


def gestion_matieres_requise(vue):
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).gere_matieres:
            raise PermissionDenied("Action réservée à la scolarité et aux chefs de programme.")
        return vue(request, *args, **kwargs)
    return wrapper


def acces_requis(vue):
    """L'utilisateur doit avoir un rôle gePed (scolarité, département ou programme)."""
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).a_acces:
            raise PermissionDenied("Votre compte n'est rattaché à aucun service.")
        return vue(request, *args, **kwargs)
    return wrapper


def attestations_requise(vue):
    """Scolarité, ou directeur d'un Master (les chefs de département n'y ont pas accès)."""
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).gere_attestations:
            raise PermissionDenied("Attestations réservées à la scolarité et aux directeurs de Master.")
        return vue(request, *args, **kwargs)
    return wrapper


def etudiants_requise(vue):
    """Scolarité, ou directeur d'un Master (pour les étudiants de ses programmes de Master)."""
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).gere_etudiants:
            raise PermissionDenied("Réservé à la scolarité et aux directeurs de Master.")
        return vue(request, *args, **kwargs)
    return wrapper


def inscriptions_gerees(profil):
    """Inscriptions que le profil gère : toutes (scolarité), celles de ses Masters (directeur), sinon aucune."""
    qs = Inscription.objects.all()
    if profil.est_scolarite:
        return qs
    return qs.filter(filiere_id__in=profil.masters_diriges) if profil.masters_diriges else qs.none()


def etudiants_geres(profil):
    """Étudiants que le profil gère : tous (scolarité), ceux inscrits dans ses Masters (directeur)."""
    from .models import Etudiant
    if profil.est_scolarite:
        return Etudiant.objects.all()
    return Etudiant.objects.filter(pk__in=inscriptions_gerees(profil).values('etudiant_id'))


def attestations_visibles(profil):
    """Attestations que le profil gère : toutes (scolarité), celles de ses Masters (directeur), sinon aucune."""
    from .models import Attestation
    qs = Attestation.objects.all()
    if profil.est_scolarite:
        return qs
    return qs.filter(filiere_id__in=profil.masters_diriges) if profil.masters_diriges else qs.none()


def scolarite_requise(vue):
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).est_scolarite:
            raise PermissionDenied("Action réservée à la scolarité.")
        return vue(request, *args, **kwargs)
    return wrapper
