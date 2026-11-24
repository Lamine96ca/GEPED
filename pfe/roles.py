"""
Rôles GePed et périmètre de visibilité des étudiants.

- Scolarité   : superutilisateur, ou personnel rattaché au service de la scolarité
                (code settings.GPED_SCOLARITE_CODE ou nom dans settings.GPED_SCOLARITE_NOMS).
                Peut inscrire, importer, modifier, exporter.
- Chef de département : personnel « chef » rattaché à un département, c'est-à-dire un service auquel
                des programmes sont rattachés (Service.departement), ou un programme qui est son propre
                département. Premier responsable des programmes du département : il a sur TOUS ces
                programmes les droits d'un directeur de programme, et il VALIDE les notes d'un semestre
                avant la délibération (après validation, ses directeurs ne les modifient plus).
- Directeur de programme : personnel rattaché à un Service programme (adjoint du chef de département).
                Gère les matières, les notes et les soutenances de son ou ses programmes.
- Département : personnel (non chef) d'un département : consulte les étudiants et les matières des
                programmes du département.
- Service     : personnel d'un autre service : consulte les étudiants et les matières de tout l'établissement.
- Direction   : Direction Générale et Direction Générale des Études (des services) : consultent tout
                l'établissement (étudiants, statistiques, demandes en ligne, congés, notes, résultats,
                validations, délibérations, historique des notes, fichiers importés, autorisations,
                soutenances) sans rien modifier : le middleware LectureSeuleMiddleware refuse toute
                modification. Restent fermés : relevés et attestations (scolarité), la partie Matières, et
                les activités du super utilisateur (personnel, guides, administration).

- Enseignant  : fiche Enseignant rattachée au compte (pfe/models.py). Un membre du personnel qui enseigne garde son
                rôle et a en plus l'espace enseignant ; un enseignant sans service n'ouvre QUE l'espace enseignant.
Périmètre : la scolarité et les services voient tout ; le chef de département, le directeur de programme
et le personnel d'un département ne voient que leurs programmes (`programme_ids`).
"""
from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied

from django.db.models import Q

from .models import Inscription, Matiere, Service

SCOLARITE = 'scolarite'
CHEF_DEPARTEMENT = 'chef_departement'
PROGRAMME = 'programme'
DEPARTEMENT = 'departement'
SERVICE = 'service'
ENSEIGNANT = 'enseignant'
AUCUN = 'aucun'

LIBELLES = {
    SCOLARITE: 'Scolarité',
    CHEF_DEPARTEMENT: 'Chef de département',
    PROGRAMME: 'Directeur de programme',
    DEPARTEMENT: 'Département',
    SERVICE: 'Service',
    ENSEIGNANT: 'Enseignant',
    AUCUN: 'Aucun accès',
}

# Rôles limités à des programmes (`programme_ids`) ; la scolarité et les services voient tout l'établissement
ROLES_PERIMETRE = (CHEF_DEPARTEMENT, PROGRAMME, DEPARTEMENT)


def programmes_des_departements(departement_ids):
    """Programmes rattachés à ces départements (un programme sans département est son propre département)."""
    ids = list(departement_ids)
    if not ids:
        return []
    return list(Service.objects.filter(programme=True)
                .filter(Q(departement_id__in=ids) | Q(pk__in=ids, departement__isnull=True)).order_by('nom'))


class Profil:
    def __init__(self, role, services=(), personnel=None, superuser=False, tous_services=None, programmes=None,
                 departements=()):
        self.role = role
        self.services = list(services)
        self.personnel = personnel
        self.superuser = superuser
        # Tous les services du personnel (le rôle n'en garde qu'une partie) : sert aux droits de chef
        self.tous_services = list(tous_services if tous_services is not None else services)
        # Programmes du périmètre (rôles limités) ; par défaut, les services programmes du rôle
        self.programmes = list(programmes if programmes is not None else [s for s in self.services if s.programme])
        self.departements = list(departements)       # départements du chef ou du personnel de département
        self.enseignant = None                         # fiche Enseignant active du compte (voir _calculer_profil)

    @property
    def est_chef(self):
        return bool(self.personnel and self.personnel.chef)

    @property
    def est_chef_scolarite(self):
        """Super utilisateur, ou chef rattaché au Service Scolarité."""
        return self.superuser or (self.est_chef and any(est_service_scolarite(s) for s in self.tous_services))

    @property
    def departements_diriges(self):
        """Départements dont le profil est chef (identifiants des services départements)."""
        return {d.id for d in self.departements} if self.role == CHEF_DEPARTEMENT else set()

    @property
    def est_chef_departement(self):
        """Chef de département : valide les notes des semestres de ses programmes avant la délibération."""
        return self.role == CHEF_DEPARTEMENT

    @property
    def voit_validations(self):
        """Page « Validation des semestres » : le chef de département (il valide) et le super utilisateur."""
        return self.est_chef_departement or self.superuser

    @property
    def voit_deliberation(self):
        """Cadre « Délibération » des résultats : la scolarité (le chef de la scolarité délibère) et la Direction, pas
        les départements ni les programmes (eux présentent les résultats au jury dans l'espace de délibération)."""
        return self.est_scolarite or self.est_direction

    @property
    def telecharge_fiches(self):
        """Fiche de notes de l'enseignant (Excel) : chef de département et directeur de programme, pas la scolarité."""
        return self.role in (CHEF_DEPARTEMENT, PROGRAMME)

    @property
    def a_perimetre(self):
        """Limité à ses programmes (chef de département, directeur de programme, personnel de département)."""
        return self.role in ROLES_PERIMETRE

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
        if self.lecture_seule:                      # « Service » ne dit rien : on nomme la direction
            return 'Direction Générale' if self.est_dg and not self.est_dge else 'Direction Générale des Études'
        return LIBELLES[self.role]

    @property
    def est_scolarite(self):
        return self.role == SCOLARITE

    @property
    def gere_exclusions(self):
        """Exclure un étudiant d'un programme : la scolarité (agents et chef) et l'administrateur."""
        return self.est_scolarite

    @property
    def reintegre_exclus(self):
        """Réintégrer un étudiant exclu : le chef de la scolarité et l'administrateur seulement."""
        return self.est_chef_scolarite

    @property
    def voit_exclusions(self):
        """Liste des exclusions (consultation) : tous les espaces du personnel, chacun pour ses étudiants."""
        return self.a_acces

    @property
    def a_acces(self):
        """Pages du personnel : tout rôle de service (pas un compte seulement enseignant)."""
        return self.role not in (AUCUN, ENSEIGNANT)

    @property
    def est_enseignant(self):
        """Le compte a une fiche Enseignant active : il ouvre l'espace enseignant."""
        return self.enseignant is not None

    @property
    def gere_enseignants(self):
        """Ajouter, modifier, désactiver les enseignants : la Direction Générale des Études et le super utilisateur."""
        return self.superuser or self.est_dge

    @property
    def gere_salles(self):
        """Salles et bâtiments (ajout, modification, import) : la Direction Générale des Études et le super utilisateur."""
        return self.superuser or self.est_dge

    @property
    def voit_salles(self):
        """Salles et bâtiments en consultation : la Direction Générale et la scolarité."""
        return self.gere_salles or self.est_direction or self.est_scolarite

    @property
    def gere_emplois(self):
        """Composer les emplois du temps : la Direction Générale des Études et le super utilisateur."""
        return self.superuser or self.est_dge

    @property
    def voit_emplois(self):
        """Emplois du temps en consultation : Direction Générale et scolarité (tous), départements et programmes
        (les leurs)."""
        return self.gere_emplois or self.est_direction or self.est_scolarite or self.a_perimetre

    @property
    def voit_enseignants(self):
        """Liste des enseignants : la DGE (gestion), la Direction Générale et la scolarité (tous), le chef de
        département et le directeur de programme (ceux de leur département)."""
        return (self.gere_enseignants or self.est_direction or self.est_scolarite
                or self.role in (CHEF_DEPARTEMENT, PROGRAMME))

    @property
    def programme_ids(self):
        return [p.id for p in self.programmes]

    @property
    def gere_matieres(self):
        """Matières, notes et soutenances (dans son périmètre) : scolarité, chef de département, directeur."""
        return self.role in (SCOLARITE, CHEF_DEPARTEMENT, PROGRAMME)

    @property
    def est_dge(self):
        """Direction Générale des Études : consulte tout (étudiants, notes, statistiques, congés), ne modifie rien."""
        return any(est_service_dge(s) for s in self.tous_services)

    @property
    def est_dg(self):
        """Direction Générale : mêmes droits de consultation que la Direction Générale des Études."""
        return any(est_service_dg(s) for s in self.tous_services)

    @property
    def est_direction(self):
        """Direction Générale ou Direction Générale des Études : consulte tout l'établissement."""
        return self.est_dge or self.est_dg

    @property
    def lecture_seule(self):
        """Compte de la Direction (sans autre rôle) : voit tout, ne modifie rien (voir LectureSeuleMiddleware)."""
        return self.est_direction and self.role == SERVICE and not self.superuser

    @property
    def consulte_demandes(self):
        """Demandes en ligne : ceux qui les gèrent, et la Direction (lecture). Relevés et attestations : scolarité."""
        return self.gere_etudiants or self.est_direction

    @property
    def voit_matieres(self):
        """Partie « Matières » : tout le personnel, sauf la Direction (elle suit les notes, pas les matières)."""
        return self.a_acces and not self.lecture_seule

    @property
    def voit_autorisations(self):
        """Autorisations de modification des notes : la scolarité seulement (comme avant l'espace Direction)."""
        return self.est_scolarite

    @property
    def voit_soutenances(self):
        """Liste et calendrier des soutenances : la scolarité et les services voient tout (lecture seule pour les
        services) ; chef de département, directeur de programme et personnel de département, leurs programmes."""
        return self.role in (SCOLARITE, SERVICE) or self.a_perimetre

    @property
    def lit_notes(self):
        """Notes et résultats : scolarité, chefs de département, directeurs, Direction Générale des Études (lecture)."""
        return self.gere_matieres or self.est_direction

    @property
    def voit_suivi_notes(self):
        """Historique des notes et fichiers importés : scolarité, admin et Direction."""
        return self.est_scolarite or self.est_direction

    @property
    def voit_conges(self):
        """Liste des congés académiques : ceux qui gèrent les étudiants, et la Direction."""
        return self.gere_etudiants or self.est_direction

    @property
    def structure(self):
        """Nom des services affichés à côté du rôle (le département pour son chef et son personnel)."""
        return ', '.join(s.nom for s in (self.departements or self.services))


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


def _sans_accents(texte):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', ' '.join(str(texte or '').split()).lower())
                   if unicodedata.category(c) != 'Mn')


def est_service_dge(service):
    """Direction Générale des Études : reconnue par son nom (GPED_DGE_NOMS), sans casse ni accents."""
    if service.programme:
        return False
    noms = {_sans_accents(n) for n in getattr(settings, 'GPED_DGE_NOMS', ['Direction Générale des Études'])}
    return _sans_accents(service.nom) in noms


def est_service_dg(service):
    """Direction Générale : reconnue par son nom (GPED_DIRECTION_GENERALE_NOMS), sans casse ni accents."""
    if service.programme:
        return False
    noms = {_sans_accents(n) for n in getattr(settings, 'GPED_DIRECTION_GENERALE_NOMS', ['Direction Générale'])}
    return _sans_accents(service.nom) in noms


def _calculer_profil(user):
    profil = _profil_selon_services(user)
    if user.is_authenticated:
        enseignant = getattr(user, 'profil_enseignant', None)
        if enseignant is not None and enseignant.actif:
            profil.enseignant = enseignant
            if profil.role == AUCUN:                   # seulement enseignant : l'espace enseignant, rien d'autre
                profil.role = ENSEIGNANT
    return profil


def departements_etablissement():
    """Départements de l'établissement : services auxquels des programmes sont rattachés, et programmes qui sont
    leur propre département (sans département de rattachement)."""
    rattaches = Service.objects.filter(programme=True, departement__isnull=False).values('departement_id')
    return Service.objects.filter(Q(pk__in=rattaches) | Q(programme=True, departement__isnull=True)).order_by('nom')


def departements_du_profil(profil):
    """Départements d'un chef de département ou d'un directeur de programme (ceux de ses programmes)."""
    return {p.departement_id or p.id for p in profil.programmes} | {d.id for d in profil.departements}


def enseignants_visibles(profil):
    """Enseignants que le profil voit : tous (DGE, Direction Générale, scolarité, super utilisateur), ceux de son
    département (chef de département, directeur de programme), sinon aucun."""
    from .models import Enseignant
    qs = Enseignant.objects.select_related('departement', 'utilisateur')
    if profil.gere_enseignants or profil.est_direction or profil.est_scolarite:
        return qs
    if profil.role in (CHEF_DEPARTEMENT, PROGRAMME):
        return qs.filter(departement_id__in=departements_du_profil(profil))
    return qs.none()


def _profil_selon_services(user):
    if not user.is_authenticated:
        return Profil(AUCUN)
    personnel = getattr(user, 'profil_personnel', None)
    services = list(personnel.service.all()) if personnel else []
    scolarite = [s for s in services if est_service_scolarite(s)]

    options = {'superuser': user.is_superuser, 'tous_services': services}
    if user.is_superuser or scolarite:
        return Profil(SCOLARITE, scolarite, personnel, **options)
    programmes = [s for s in services if s.programme]
    # Départements de la personne : ses services auxquels des programmes sont rattachés
    du_departement = programmes_des_departements([s.id for s in services])
    dep_ids = {p.departement_id or p.id for p in du_departement}
    departements = [s for s in services if s.id in dep_ids]
    if departements and personnel and personnel.chef:
        perimetre = {p.id: p for p in du_departement + programmes}
        return Profil(CHEF_DEPARTEMENT, departements, personnel, departements=departements,
                      programmes=sorted(perimetre.values(), key=lambda p: p.nom), **options)
    if programmes:
        return Profil(PROGRAMME, programmes, personnel, **options)
    if departements:
        return Profil(DEPARTEMENT, departements, personnel, programmes=du_departement, departements=departements,
                      **options)
    if services:
        return Profil(SERVICE, services, personnel, programmes=[], **options)
    return Profil(AUCUN, [], personnel, **options)


def get_profil(request):
    """Profil mis en cache sur la requête."""
    if not hasattr(request, '_gped_profil'):
        request._gped_profil = _calculer_profil(request.user)
    return request._gped_profil


def inscriptions_visibles(profil):
    """QuerySet des inscriptions que le profil a le droit de voir."""
    qs = Inscription.objects.all()
    if profil.role in (SCOLARITE, SERVICE):
        return qs
    if profil.a_perimetre:
        return qs.filter(filiere_id__in=profil.programme_ids)
    return qs.none()


def matieres_visibles(profil):
    """QuerySet des matières que le profil a le droit de voir."""
    qs = Matiere.objects.all()
    if profil.role in (SCOLARITE, SERVICE):
        return qs
    if profil.a_perimetre:
        return qs.filter(filiere_id__in=profil.programme_ids)
    return qs.none()


def programmes_du_profil(profil):
    """Programmes proposés dans les listes : tous (scolarité, autres services), sinon ceux de son périmètre."""
    qs = Service.objects.filter(programme=True).order_by('nom')
    if profil.a_perimetre:
        return qs.filter(id__in=profil.programme_ids)
    return qs if profil.role in (SCOLARITE, SERVICE) else qs.none()


def gestion_matieres_requise(vue):
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).gere_matieres:
            raise PermissionDenied("Action réservée à la scolarité, aux chefs de département et aux directeurs de "
                                   "programme.")
        return vue(request, *args, **kwargs)
    return wrapper


def _exige(propriete, message):
    def decorateur(vue):
        @wraps(vue)
        def wrapper(request, *args, **kwargs):
            if not getattr(get_profil(request), propriete):
                raise PermissionDenied(message)
            return vue(request, *args, **kwargs)
        return wrapper
    return decorateur


lecture_notes_requise = _exige('lit_notes', "Notes : réservé à la scolarité, aux chefs de département, aux "
                                            "directeurs de programme et à la Direction.")
demandes_lecture_requise = _exige('consulte_demandes', "Demandes en ligne : réservé à la scolarité, aux directeurs "
                                                       "de Master et à la Direction.")
matieres_lecture_requise = _exige('voit_matieres', "Matières : réservé à la scolarité, aux départements et aux "
                                                   "programmes.")
autorisations_lecture_requise = _exige('voit_autorisations', "Réservé à la scolarité et à la Direction.")
chef_departement_requis = _exige('est_chef_departement', "Réservé au chef de département.")
validations_requises = _exige('voit_validations', "Validation des semestres : réservé aux chefs de département et "
                                                  "au super utilisateur.")
enseignants_lecture_requise = _exige('voit_enseignants', "Enseignants : réservé à la Direction des Études, à la "
                                                          "Direction Générale, à la scolarité et aux départements.")
enseignants_gestion_requise = _exige('gere_enseignants', "Gestion des enseignants : réservée à la Direction "
                                                         "Générale des Études.")
salles_lecture_requise = _exige('voit_salles', "Salles et bâtiments : réservé à la Direction des Études, à la "
                                                 "Direction Générale et à la scolarité.")
salles_gestion_requise = _exige('gere_salles', "Gestion des salles et des bâtiments : réservée à la Direction "
                                               "Générale des Études.")
emplois_lecture_requise = _exige('voit_emplois', "Emplois du temps : réservé au personnel concerné.")
emplois_gestion_requise = _exige('gere_emplois', "Composer les emplois du temps : réservé à la Direction Générale "
                                                 "des Études.")
espace_enseignant_requis = _exige('est_enseignant', "Espace réservé aux enseignants.")
soutenances_lecture_requise = _exige('voit_soutenances', "Soutenances : réservé au personnel de l'établissement.")
fiches_requises = _exige('telecharge_fiches', "Fiche de notes de l'enseignant : réservé aux chefs de département et "
                                              "aux directeurs de programme.")
suivi_notes_requis = _exige('voit_suivi_notes', "Réservé à la scolarité, à l'administrateur et à la Direction.")
lecture_conges_requise = _exige('voit_conges', "Congés académiques : réservé à la scolarité et à la Direction.")
chef_scolarite_requis = _exige('est_chef_scolarite', "Réservé au chef de la scolarité et à l'administrateur.")
exclusions_lecture_requise = _exige('voit_exclusions', "Exclusions : réservé au personnel.")
exclusions_gestion_requise = _exige('gere_exclusions', "Seule la scolarité prononce une exclusion.")
reintegration_requise = _exige('reintegre_exclus', "Seuls le chef de la scolarité et l'administrateur réintègrent "
                                                   "un étudiant exclu.")


def acces_requis(vue):
    """L'utilisateur doit avoir un rôle GePed (scolarité, département, programme ou autre service). Un compte
    seulement enseignant est conduit à son espace."""
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        profil = get_profil(request)
        if profil.role == ENSEIGNANT:
            from django.shortcuts import redirect
            return redirect('espace_enseignant')
        if not profil.a_acces:
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


def conges_requis(vue):
    """Accorder, modifier, lever ou annuler un congé académique : chef de la scolarité ou super utilisateur."""
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).est_chef_scolarite:
            raise PermissionDenied("Congés académiques : réservé au chef de la scolarité et à l'administrateur.")
        return vue(request, *args, **kwargs)
    return wrapper


def scolarite_requise(vue):
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not get_profil(request).est_scolarite:
            raise PermissionDenied("Action réservée à la scolarité.")
        return vue(request, *args, **kwargs)
    return wrapper
