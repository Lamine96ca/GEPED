# Les tables Groupe et Soutenance étaient vides : elles sont remplacées (migration suivante)
# par ProjetSoutenance, Soutenance (avec promotion, note de session, verrouillage) et SoutenanceHistorique.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0012_notes_nouveaux_modeles'),
    ]

    operations = [
        migrations.DeleteModel(name='Soutenance'),
        migrations.DeleteModel(name='Groupe'),
    ]
