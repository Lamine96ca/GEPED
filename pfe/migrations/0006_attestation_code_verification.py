"""
Code de vérification (QR) des attestations : ajouté vide, rempli pour les attestations
existantes (un code différent pour chacune), puis rendu unique.
"""
import secrets

from django.db import migrations, models

import pfe.models


def remplir_codes(apps, schema_editor):
    Attestation = apps.get_model('pfe', 'Attestation')
    for a in Attestation.objects.filter(code_verification__isnull=True):
        a.code_verification = secrets.token_urlsafe(16)
        a.save(update_fields=['code_verification'])


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0005_compteur_attestation'),
    ]

    operations = [
        migrations.AddField(
            model_name='attestation',
            name='code_verification',
            field=models.CharField(max_length=32, null=True, editable=False, verbose_name='Code de vérification'),
        ),
        migrations.RunPython(remplir_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='attestation',
            name='code_verification',
            field=models.CharField(default=pfe.models.nouveau_code_verification, editable=False, max_length=32,
                                   unique=True, verbose_name='Code de vérification'),
        ),
    ]
