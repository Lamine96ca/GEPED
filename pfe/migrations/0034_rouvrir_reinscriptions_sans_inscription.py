"""
Réinscriptions en ligne « Validées » dont l'inscription de la session a été supprimée ensuite :
elles repassent « En attente », l'étudiant peut refaire sa demande (données seulement, sans retour arrière).
"""
from django.db import migrations
from django.utils import timezone


def rouvrir(apps, schema_editor):
    DemandeEtudiant = apps.get_model('pfe', 'DemandeEtudiant')
    Inscription = apps.get_model('pfe', 'Inscription')
    inscrits = set(Inscription.objects.values_list('etudiant_id', 'session_id'))
    a_rouvrir = [d.pk for d in DemandeEtudiant.objects.filter(type='reinscription', statut='validee')
                 if (d.etudiant_id, d.session_id) not in inscrits]
    DemandeEtudiant.objects.filter(pk__in=a_rouvrir).update(statut='en_attente', traitee_le=None, traitee_par=None,
                                                             date_maj=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0033_pieces_dossier_master'),
    ]

    operations = [
        migrations.RunPython(rouvrir, migrations.RunPython.noop),
    ]
