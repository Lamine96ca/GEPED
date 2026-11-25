"""
Signaux de l'application.

Inscription supprimée (liste des étudiants, espace admin…) : la réinscription en ligne validée de cette
session repasse « En attente », pour que l'étudiant puisse refaire sa demande (voir espace_etudiant.py).
"""
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Inscription


@receiver(post_delete, sender=Inscription, dispatch_uid='pfe.rouvrir_demandes')
def rouvrir_demande_en_ligne(sender, instance, **kwargs):
    from .espace_etudiant import rouvrir_demandes
    if instance.etudiant_id and instance.session_id:
        rouvrir_demandes(instance.etudiant_id, instance.session_id)


@receiver(user_logged_in, dispatch_uid='pfe.debut_presentation')
def debut_presentation(sender, request, user, **kwargs):
    """Compte de présentation : l'heure d'utilisation part de la PREMIÈRE connexion ; la session s'arrête à la fin."""
    from django.utils import timezone
    personnel = getattr(user, 'profil_personnel', None)
    if personnel is None or not personnel.presentation:
        return
    if personnel.presentation_debut is None:
        personnel.presentation_debut = timezone.now()
        personnel.save(update_fields=['presentation_debut'])
    reste = int((personnel.presentation_fin - timezone.now()).total_seconds())
    if request is not None and hasattr(request, 'session'):
        request.session.set_expiry(max(1, reste))
