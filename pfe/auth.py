"""Connexion avec l'identifiant OU l'adresse e-mail (du compte ou de la fiche personnel)."""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.db.models import Q


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or '@' not in username or password is None:
            return None
        comptes = list(User.objects.filter(Q(email__iexact=username.strip())
                                           | Q(profil_personnel__email__iexact=username.strip())).distinct()[:2])
        if len(comptes) != 1:                     # adresse inconnue ou partagée : pas de connexion ambiguë
            User().set_password(password)         # même temps de réponse (pas d'indice sur l'existence)
            return None
        user = comptes[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
