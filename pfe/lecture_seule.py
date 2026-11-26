"""
Comptes en consultation seule (Direction Générale, Direction Générale des Études) : ils voient tout, mais aucune
modification ne passe. Toute requête qui pourrait modifier (POST, PUT, PATCH, DELETE) est refusée, quelle que soit la
page ; seules restent permises la déconnexion, la gestion de son propre compte (mot de passe, e-mail, photo) et,
pour la Direction Générale des Études, la gestion des salles, des bâtiments et des emplois du temps, les dates des
semestres et les cours dispensés (relance, réajustement des heures). La CIAQ consulte aussi, sauf ses paramètres des
heures.
Les boutons de modification sont aussi masqués dans les pages, mais c'est ce middleware qui fait foi.

Compte de présentation (Personnel.presentation) : il voit tout comme le chef de la scolarité et imprime (les PDF
sont des pages en lecture), mais aucune modification ne passe, pas même celle de son propre compte. Une heure après
sa première connexion (GPED_PRESENTATION_MINUTES), il est déconnecté et désactivé ; le super utilisateur le relance
depuis la fiche du personnel.
"""
from django.contrib import messages
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .roles import get_profil

METHODES_SURES = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
PERMISES = {'logout', 'login', 'connexion_code', 'connexion_recommencer', 'mon_compte', 'mon_compte_photo',
            'diplome_verifier', 'plan_confirmer'}    # pages publiques (diplôme, délégué)
# Exceptions à la consultation : la Direction Générale des Études gère les salles, les bâtiments, les emplois du
# temps, les dates des semestres et les cours dispensés ; la CIAQ, les paramètres des heures
GESTION_COURS = {'dates_semestres', 'cours_dispenses', 'cours_dispense'}
GESTION_PARAMETRES = {'ciaq_parametres'}
GESTION_EMPLOIS = {'seance_enregistrer', 'seance_supprimer'}
GESTION_SALLES = {'batiment_enregistrer', 'batiment_supprimer', 'salle_enregistrer', 'salle_supprimer',
                  'salles_importer'}


class LectureSeuleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and get_profil(request).est_presentation:
            reponse = _presentation(request)
            if reponse is not None:
                return reponse
        if request.method not in METHODES_SURES and request.user.is_authenticated:
            profil, nom = get_profil(request), _nom_url(request)
            permise = (nom in PERMISES or (nom in GESTION_COURS and profil.reajuste_cours)
                       or (nom in GESTION_PARAMETRES and profil.gere_parametres_heures)
                       or (nom in GESTION_SALLES and profil.gere_salles)
                       or (nom in GESTION_EMPLOIS and profil.gere_emplois))
            if (profil.lecture_seule or profil.ciaq_seule) and not permise:
                raise PermissionDenied("Compte en consultation : il voit, mais ne modifie rien.")
        return self.get_response(request)


def _nom_url(request):
    try:
        return resolve(request.path_info).url_name
    except Resolver404:
        return None


PERMISES_PRESENTATION = {'logout', 'login', 'connexion_code', 'connexion_recommencer', 'diplome_verifier',
                         'plan_confirmer'}


def _presentation(request):
    """Compte de présentation : fermé une fois l'heure passée ; toute modification refusée."""
    personnel = get_profil(request).personnel
    if personnel.presentation_expiree:
        user = request.user
        logout(request)
        if user.is_active:
            user.is_active = False
            user.save(update_fields=['is_active'])
        messages.error(request, "La présentation est terminée : ce compte a été fermé une heure après sa première "
                                "connexion. Demandez au super utilisateur de le relancer.")
        return redirect('login')
    if request.method not in METHODES_SURES and _nom_url(request) not in PERMISES_PRESENTATION:
        raise PermissionDenied("Compte de présentation : consultation et impression seulement, aucune modification.")
    return None
