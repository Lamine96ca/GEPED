"""
Master : mentions Excellent, Remarquable, Satisfaisant, Acceptable (règlement « master », config/settings.py).
Les notes déjà enregistrées des étudiants de Master prennent ce règlement, puis leurs résultats (et leurs mentions)
sont recalculés. Rien à faire sur une base sans étudiant de Master (base neuve, tests).
"""
from django.db import migrations


def appliquer(apps, schema_editor):
    Inscription = apps.get_model('pfe', 'Inscription')
    ids = list(Inscription.objects.filter(cycle='Master').values_list('etudiant_id', flat=True).distinct())
    if not ids:
        return
    from pfe.notes import actualiser_reglements, recalculer
    actualiser_reglements(ids)
    recalculer(ids)


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0043_type_cours_tout'),
    ]

    operations = [
        migrations.RunPython(appliquer, migrations.RunPython.noop),
    ]
