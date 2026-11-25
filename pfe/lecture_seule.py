"""
Comptes en consultation seule (Direction Générale, Direction Générale des Études) : ils voient tout, mais aucune
modification ne passe. Toute requête qui pourrait modifier (POST, PUT, PATCH, DELETE) est refusée, quelle que soit la
page ; seules restent permises la déconnexion, la gestion de son propre compte (mot de passe, e-mail, photo) et,
pour la Direction Générale des Études, la gestion des enseignants, des salles, des bâtiments et des emplois du temps.
Les boutons de modification sont aussi masqués dans les pages, mais c'est ce middleware qui fait foi.
"""
from django.core.exceptions import PermissionDenied
from django.urls import Resolver404, resolve

from .roles import get_profil

METHODES_SURES = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
PERMISES = {'logout', 'login', 'connexion_code', 'connexion_recommencer', 'mon_compte', 'mon_compte_photo',
            'diplome_verifier'}                      # page publique de vérification d'un diplôme
# Exceptions à la consultation : la Direction Générale des Études gère les enseignants, les salles et les bâtiments
GESTION_ENSEIGNANTS = {'enseignant_creer', 'enseignant_modifier', 'enseignant_action'}
GESTION_EMPLOIS = {'seance_enregistrer', 'seance_supprimer'}
GESTION_SALLES = {'batiment_enregistrer', 'batiment_supprimer', 'salle_enregistrer', 'salle_supprimer',
                  'salles_importer'}


class LectureSeuleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in METHODES_SURES and request.user.is_authenticated:
            profil, nom = get_profil(request), _nom_url(request)
            permise = (nom in PERMISES or (nom in GESTION_ENSEIGNANTS and profil.gere_enseignants)
                       or (nom in GESTION_SALLES and profil.gere_salles)
                       or (nom in GESTION_EMPLOIS and profil.gere_emplois))
            if profil.lecture_seule and not permise:
                raise PermissionDenied("Compte en consultation : la Direction voit tout mais ne modifie rien.")
        return self.get_response(request)


def _nom_url(request):
    try:
        return resolve(request.path_info).url_name
    except Resolver404:
        return None
