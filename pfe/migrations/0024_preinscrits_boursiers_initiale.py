"""
Préinscrits : boursiers et en formation initiale par défaut (cases vides seulement).
Les autres étudiants choisissent dans leur espace de réinscription.
"""
from django.db import migrations


def remplir(apps, schema_editor):
    Etudiant = apps.get_model('pfe', 'Etudiant')
    Etudiant.objects.filter(preinscrit=True, boursier='').update(boursier='boursier')
    Etudiant.objects.filter(preinscrit=True, type_formation='').update(type_formation='initiale')


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0023_espace_etudiant'),
    ]

    operations = [
        migrations.RunPython(remplir, migrations.RunPython.noop),
    ]
