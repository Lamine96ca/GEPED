"""
Signaux de l'application.

Inscription supprimée (liste des étudiants, espace admin…) : la réinscription en ligne validée de cette
session repasse « En attente », pour que l'étudiant puisse refaire sa demande (voir espace_etudiant.py).
"""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Inscription


@receiver(post_delete, sender=Inscription, dispatch_uid='pfe.rouvrir_demandes')
def rouvrir_demande_en_ligne(sender, instance, **kwargs):
    from .espace_etudiant import rouvrir_demandes
    if instance.etudiant_id and instance.session_id:
        rouvrir_demandes(instance.etudiant_id, instance.session_id)
