"""
Règlement suivi par chaque note (settings.GPED_NOTES['reglements'], ex. arrêté 877 pour le Tronc commun
et le cycle Ingénieur à partir de 2025-2026). Les notes déjà enregistrées des étudiants concernés passent
sous leur règlement (moyenne, rattrapage plafonné), puis leurs résultats sont recalculés (mentions,
moyenne générale cumulative). Les autres notes ne changent pas.
"""
import re

from django.conf import settings
from django.db import migrations, models


def _debut(annee):
    m = re.match(r'\s*(\d{4})', str(annee or ''))
    return int(m.group(1)) if m else 0


def appliquer_reglements(apps, schema_editor):
    Inscription = apps.get_model('pfe', 'Inscription')
    ids = set()
    for code, r in settings.GPED_NOTES.get('reglements', {}).items():
        for etu, annee in Inscription.objects.filter(cycle__in=r.get('cycles', ())).values_list(
                'etudiant_id', 'session__annee'):
            if _debut(annee) >= r.get('premiere_session', 0):
                ids.add(etu)
    if not ids:
        return
    from pfe.notes import recalculer                  # actualise le règlement des notes, puis les résultats
    ids = sorted(ids)
    for i in range(0, len(ids), 300):
        recalculer(ids[i:i + 300])


class Migration(migrations.Migration):

    dependencies = [
        ('pfe', '0036_suivi_notes_autorisations'),
    ]

    operations = [
        migrations.AddField(
            model_name='note',
            name='reglement',
            field=models.CharField(blank=True, default='', editable=False, max_length=10, verbose_name='Règlement'),
        ),
        migrations.RunPython(appliquer_reglements, migrations.RunPython.noop),
    ]
