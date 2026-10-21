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
    def __init__(self, role, services=(), personnel=None):
        self.role = role
        self.services = list(services)
        self.personnel = personnel

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

    if user.is_superuser or scolarite:
        return Profil(SCOLARITE, scolarite, personnel)
    programmes = [s for s in services if s.programme]
    if programmes:
        return Profil(PROGRAMME, programmes, personnel)
    if services:
        return Profil(DEPARTEMENT, services, personnel)
    return Profil(AUCUN, [], personnel)


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


def scolarite_requise(vue):
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).est_scolarite:
            raise PermissionDenied("Action réservée à la scolarité.")
        return vue(request, *args, **kwargs)
    return wrapper
