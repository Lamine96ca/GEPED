"""
Comptes en consultation seule (Direction Générale, Direction Générale des Études) : ils voient tout, mais aucune
modification ne passe. Toute requête qui pourrait modifier (POST, PUT, PATCH, DELETE) est refusée, quelle que soit la
page ; seules restent permises la déconnexion et la gestion de son propre compte (mot de passe, e-mail, photo).
Les boutons de modification sont aussi masqués dans les pages, mais c'est ce middleware qui fait foi.
"""
from django.core.exceptions import PermissionDenied
from django.urls import Resolver404, resolve

from .roles import get_profil

METHODES_SURES = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
PERMISES = {'logout', 'login', 'connexion_code', 'connexion_recommencer', 'mon_compte', 'mon_compte_photo'}


class LectureSeuleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in METHODES_SURES and request.user.is_authenticated:
            if get_profil(request).lecture_seule and _nom_url(request) not in PERMISES:
                raise PermissionDenied("Compte en consultation : la Direction voit tout mais ne modifie rien.")
        return self.get_response(request)


def _nom_url(request):
    try:
        return resolve(request.path_info).url_name
    except Resolver404:
        return None
