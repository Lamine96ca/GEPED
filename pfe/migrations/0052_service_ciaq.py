"""Service CIAQ (Cellule interne de contrôle de qualité) : créé s'il n'existe pas (voir roles.est_service_ciaq)."""
from django.db import migrations

NOM = 'Cellule Interne de Contrôle de Qualité'


def creer(apps, schema_editor):
    Service = apps.get_model('pfe', 'Service')
    if not Service.objects.filter(code='CIAQ').exists() and not Service.objects.filter(nom__iexact=NOM).exists():
        Service.objects.create(nom=NOM, code='CIAQ', programme=False,
                               description="Contrôle de la qualité des enseignements : emploi du temps, avancement "
                                           "des cours, heures des enseignants et paramètres des heures "
                                           "supplémentaires.")


class Migration(migrations.Migration):
    dependencies = [('pfe', '0051_seances_cours_ciaq')]
    operations = [migrations.RunPython(creer, migrations.RunPython.noop)]
