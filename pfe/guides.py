"""
Guides d'utilisation (PDF) dans chaque compte.

- Chaque type de compte a son guide : scolarité (et super utilisateur), chef de département, directeur de
  programme, Direction Générale des Études. Les autres comptes n'en ont pas encore.
- Le super utilisateur dépose les PDF (page « Guides d'utilisation ») ; chaque dépôt est une nouvelle version,
  avec ses nouveautés ; ils sont rangés dans le stockage privé (jamais publics : les captures montrent des
  étudiants).
- Le guide s'ouvre depuis le tableau de bord, le menu du compte et le menu latéral. Tant qu'un utilisateur n'a
  pas ouvert la dernière version de son guide, un bandeau « Nouveautés du guide » s'affiche sur toutes les pages.
"""
from .models import GuideUtilisation, LectureGuide


def public_du_profil(profil):
    """Guide de ce profil (un code de GuideUtilisation.PUBLICS), ou '' s'il n'y en a pas encore."""
    if profil.est_scolarite:
        return GuideUtilisation.SCOLARITE
    if profil.est_chef_departement:
        return GuideUtilisation.CHEF_DEPARTEMENT
    if profil.role == 'programme':
        return GuideUtilisation.DIRECTEUR
    if profil.est_direction:                    # Direction Générale et Direction Générale des Études
        return GuideUtilisation.DGE
    return ''


def guide_courant(public):
    """Dernière version déposée du guide de ce public (ou None)."""
    if not public:
        return None
    return GuideUtilisation.objects.filter(public=public).order_by('-date', '-id').first()


def etat_pour(user, profil):
    """{'guide': dernière version (ou None), 'nouveau': True si l'utilisateur ne l'a pas encore ouverte}."""
    guide = guide_courant(public_du_profil(profil))
    if guide is None:
        return {'guide': None, 'nouveau': False}
    return {'guide': guide, 'nouveau': not LectureGuide.objects.filter(utilisateur=user, guide=guide).exists()}


def noter_lecture(user, guide):
    LectureGuide.objects.get_or_create(utilisateur=user, guide=guide)


def ouvrir(guide):
    from .models import stockage_prive
    return stockage_prive().open(guide.fichier.name, 'rb')
