# L'ancienne table ReleveNote était vide : elle est remplacée (migration suivante) par ReleveNote
# (type semestre / annuel / extrait, QR de vérification) et CompteurReleve (numérotation propre aux relevés).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0015_attestation_soutenance'),
    ]

    operations = [
        migrations.DeleteModel(name='ReleveNote'),
    ]
