"""
Le cycle passe de l'étudiant à l'inscription, avec la nouvelle liste
DUT / Tronc commun / Licence / Ingénieur / Master / Doctorat.

Les valeurs existantes sont recopiées sur toutes les inscriptions de l'étudiant :
anciens codes 1 → Licence, 2 → Ingénieur, 3 → Master, 4 → Doctorat.
"""
from django.db import migrations, models

ANCIEN_VERS_NOUVEAU = {'1': 'LIC', '2': 'ING', '3': 'MAS', '4': 'DOC'}
NOUVEAU_VERS_ANCIEN = {v: k for k, v in ANCIEN_VERS_NOUVEAU.items()}


def recopier_cycle(apps, schema_editor):
    Etudiant = apps.get_model('pfe', 'Etudiant')
    Inscription = apps.get_model('pfe', 'Inscription')
    for ancien, nouveau in ANCIEN_VERS_NOUVEAU.items():
        ids = Etudiant.objects.filter(cycle=ancien).values('id')
        Inscription.objects.filter(etudiant_id__in=ids).update(cycle=nouveau)


def restaurer_cycle(apps, schema_editor):
    Etudiant = apps.get_model('pfe', 'Etudiant')
    Inscription = apps.get_model('pfe', 'Inscription')
    for nouveau, ancien in NOUVEAU_VERS_ANCIEN.items():
        ids = Inscription.objects.filter(cycle=nouveau).values('etudiant_id')
        Etudiant.objects.filter(id__in=ids).update(cycle=ancien)


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0002_date_naissance_default_none'),
    ]

    operations = [
        migrations.AddField(
            model_name='inscription',
            name='cycle',
            field=models.CharField(blank=True, choices=[('DUT', 'DUT'), ('TC', 'Tronc commun'), ('LIC', 'Licence'), ('ING', 'Ingénieur'), ('MAS', 'Master'), ('DOC', 'Doctorat')], max_length=3, null=True, verbose_name="Cycle d'étude"),
        ),
        migrations.RunPython(recopier_cycle, restaurer_cycle),
        migrations.RemoveField(
            model_name='etudiant',
            name='cycle',
        ),
    ]
