"""
Données de démonstration gePed (développement uniquement).

    python manage.py gped_demo --etudiants 10000

Crée les référentiels, des comptes de test et N étudiants inscrits sur la session en cours.
Comptes créés (mot de passe commun : MOT_DE_PASSE_DEMO ci-dessous) :
    admin        superutilisateur (scolarité + paramétrage)
    scolarite    personnel du Service Scolarité
    departement  personnel de la Direction Générale Des Etudes (non-programme)
    prog_info    personnel du programme Génie Informatique
"""
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from pfe.models import Etudiant, Inscription, Licence, Personnel, Promotion, Semestre, Service, Session

MOT_DE_PASSE_DEMO = 'gped-demo-2026'

PRENOMS_M = ['Mamadou', 'Alpha', 'Ibrahima', 'Ousmane', 'Sekou', 'Mohamed', 'Abdoulaye', 'Thierno', 'Lansana', 'Moussa']
PRENOMS_F = ['Mariama', 'Fatoumata', 'Aissatou', 'Kadiatou', 'Hawa', 'Fanta', 'Djenabou', 'Aminata', 'Binta', 'Oumou']
NOMS = ['DIALLO', 'BAH', 'BARRY', 'SOW', 'CAMARA', 'CONDE', 'SYLLA', 'TOURE', 'KEITA', 'SOUARE', 'KOUROUMA', 'CISSE']
PROGRAMMES = [('Informatique', 'INFO'), ('Génie civil', 'GC'), ('Gestion', 'GEST'),
              ('Électrotechnique', 'ELEC'), ('Tronc commun', 'TC')]


class Command(BaseCommand):
    help = "Crée des données de démonstration (référentiels, comptes, étudiants)."

    def add_arguments(self, parser):
        parser.add_argument('--etudiants', type=int, default=2000)

    @transaction.atomic
    def handle(self, *args, **opts):
        session, _ = Session.objects.get_or_create(annee='2025 - 2026', defaults={'en_cours': True})
        Session.objects.get_or_create(annee='2024 - 2025')
        licences = [Licence.objects.get_or_create(nom=f'LICENCE {i}')[0] for i in (1, 2, 3)]
        semestres = {}
        for i, lic in enumerate(licences):
            semestres[lic.id] = [Semestre.objects.get_or_create(
                nom=f'Semestre {2 * i + k}', licence=lic, defaults={'abrege': f'S{2 * i + k}'})[0] for k in (1, 2)]
        promotions = [Promotion.objects.get_or_create(nom=f'P{n}')[0] for n in (18, 19, 20)]

        # Services réels de l'IST (pfe_service) ; ceux de démo seulement si la table est vide
        scol, _ = Service.objects.get_or_create(nom='Service Scolarité', defaults={'code': '0000'})
        dep, _ = Service.objects.get_or_create(nom='Direction Générale Des Etudes', defaults={'code': '0000'})
        programmes = list(Service.objects.filter(programme=True).order_by('id'))
        if not programmes:
            programmes = [Service.objects.get_or_create(nom=n, defaults={'code': c, 'programme': True})[0]
                          for n, c in PROGRAMMES]
        info = Service.objects.filter(programme=True, nom__icontains='INFORMATIQUE').first() or programmes[0]

        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@gped.local', MOT_DE_PASSE_DEMO)
        for i, (login, service) in enumerate([('scolarite', scol), ('departement', dep), ('prog_info', info)]):
            user, cree = User.objects.get_or_create(username=login)
            if cree:
                user.set_password(MOT_DE_PASSE_DEMO)
                user.save()
            pers, _ = Personnel.objects.get_or_create(utilisateur=user, defaults={
                'prenom': login.capitalize(), 'nom': 'DEMO', 'telephone': f'60000000{i}',
                'email': f'{login}@gped.local', 'fonc': 'Agent'})
            pers.service.add(service)

        deja = Etudiant.objects.filter(matricule__startswith='DEMO').count()
        n = opts['etudiants']
        rnd = random.Random(42)
        etudiants = []
        for k in range(deja, deja + n):
            f = rnd.random() < 0.45
            etudiants.append(Etudiant(
                matricule=f'DEMO{k:06d}', prenom=rnd.choice(PRENOMS_F if f else PRENOMS_M), nom=rnd.choice(NOMS),
                sexe='F' if f else 'M', date_de_naissance=None, telephone=f'6{rnd.randint(20000000, 69999999)}',
                email=f'demo{k:06d}@etudiant.gped', nationalite='Guinéenne', ine=f'INE{k:08d}'))
        Etudiant.objects.bulk_create(etudiants, batch_size=1000)
        ids = Etudiant.objects.filter(matricule__in=[e.matricule for e in etudiants]).values_list('id', flat=True)
        inscriptions = []
        for etu_id in ids:
            lic = rnd.choice(licences)
            prog = rnd.choice(programmes)
            inscriptions.append(Inscription(
                etudiant_id=etu_id, session=session, licence=lic, semestre=rnd.choice(semestres[lic.id]),
                filiere=prog, promotion=rnd.choice(promotions), redouble=rnd.random() < 0.08,
                cycle='Tronc commun' if 'TRONC' in prog.nom.upper() else 'Licence'))
        Inscription.objects.bulk_create(inscriptions, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f'{n} étudiants de démonstration créés (session {session}).'))
