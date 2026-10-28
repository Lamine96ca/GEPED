"""
Compte de démonstration du programme MASTER EN ENERGIE (local ou production) :

    python manage.py gped_demo_master
    python manage.py gped_demo_master --email-etudiant mon.adresse@gmail.com
    python manage.py gped_demo_master --supprimer

Crée, s'ils n'existent pas (la commande peut être relancée sans rien dupliquer) :
  - le programme MASTER EN ENERGIE (cycle Master, département ÉNERGÉTIQUE), les niveaux MASTER 1
    et MASTER 2, la promotion PM01 prévue en MASTER 1 pour la session en cours ;
  - le compte « demo.master » : chef du programme (il signe les documents de ce Master) ;
  - une étudiante préinscrite en MASTER 1, pour essayer l'onglet « Master » de l'espace étudiant
    (--email-etudiant : une adresse qui reçoit vraiment le lien, en production).
Le mot de passe du compte est tiré au hasard et affiché UNE fois dans ce terminal (jamais enregistré
dans le code) ; --nouveau-mot-de-passe en tire un autre. --supprimer retire le compte et l'étudiante
de démonstration (le programme, les niveaux et la promotion restent).
"""
import secrets
import string

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pfe.models import (Etudiant, Inscription, Licence, Personnel, Promotion, Promotion_Licence_Session, Service,
                        Session)

IDENTIFIANT = 'demo.master'
EMAIL_COMPTE = 'demo.master@ist-mamou.local'
EMAIL_ETUDIANT = 'etudiant.master@ist-mamou.local'
MATRICULE_ETUDIANT = '26M03DEMO01'


def _mot_de_passe():
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))


class Command(BaseCommand):
    help = "Crée (ou supprime) le compte de démonstration du programme MASTER EN ENERGIE."

    def add_arguments(self, parser):
        parser.add_argument('--email-etudiant', default=EMAIL_ETUDIANT,
                            help="Adresse de l'étudiante de démonstration (reçoit le lien de l'onglet Master)")
        parser.add_argument('--nouveau-mot-de-passe', action='store_true',
                            help="Tire un nouveau mot de passe pour un compte déjà créé")
        parser.add_argument('--supprimer', action='store_true', help="Retire le compte et l'étudiante de démonstration")

    def handle(self, *args, **opts):
        if opts['supprimer']:
            return self.supprimer()
        session = Session.objects.filter(en_cours=True).order_by('-annee').first()
        if session is None:
            raise CommandError("Aucune session « en cours » : cochez-en une dans l'administration, puis relancez.")
        mot_de_passe = None
        with transaction.atomic():
            departement = Service.objects.filter(nom__iexact='ENERGETIQUE').first()
            programme, cree = Service.objects.get_or_create(nom='MASTER EN ENERGIE', defaults={
                'code': 'M03', 'programme': True, 'cycle': 'Master', 'description': 'Master en Énergie',
                'departement': departement})
            m1, _ = Licence.objects.get_or_create(nom='MASTER 1')
            Licence.objects.get_or_create(nom='MASTER 2')
            promo, _ = Promotion.objects.get_or_create(
                nom='PM01', defaults={'description': 'Première promotion du Master en Énergie'})
            Promotion_Licence_Session.objects.get_or_create(promotion=promo, licence=m1, session=session)

            user = User.objects.filter(username=IDENTIFIANT).first()
            if user is None:
                mot_de_passe = _mot_de_passe()
                user = User.objects.create_user(IDENTIFIANT, email=EMAIL_COMPTE, password=mot_de_passe,
                                                first_name='Démo', last_name='MASTER ÉNERGIE')
            elif opts['nouveau_mot_de_passe']:
                mot_de_passe = _mot_de_passe()
                user.set_password(mot_de_passe)
                user.save(update_fields=['password'])
            personnel, _ = Personnel.objects.get_or_create(utilisateur=user, defaults={
                'civilite': 'Dr', 'prenom': 'Démo', 'nom': 'Master Énergie', 'telephone': '+224 600 00 00 99',
                'email': EMAIL_COMPTE, 'fonc': 'Le Chef du programme Master en Énergie', 'chef': True})
            personnel.service.add(programme)

            email = opts['email_etudiant'].strip().lower()
            etudiant = Etudiant.objects.filter(matricule=MATRICULE_ETUDIANT).first()
            if etudiant is None:
                if Etudiant.objects.filter(email__iexact=email).exists():
                    raise CommandError(f"L'adresse {email} appartient déjà à un autre étudiant : choisissez-en une autre "
                                       f"avec --email-etudiant.")
                etudiant = Etudiant.objects.create(
                    matricule=MATRICULE_ETUDIANT, prenom='Mariama', nom='DIALLO', email=email, ine='DEMOMASTER01',
                    pvbac='000001', preinscrit=True, sexe='F', nationalite='Guinéenne')
            elif etudiant.email.lower() != email and opts['email_etudiant'] != EMAIL_ETUDIANT:
                if Etudiant.objects.filter(email__iexact=email).exclude(pk=etudiant.pk).exists():
                    raise CommandError(f"L'adresse {email} appartient déjà à un autre étudiant.")
                etudiant.email = email
                etudiant.save(update_fields=['email'])
            Inscription.objects.get_or_create(etudiant=etudiant, session=session, defaults={
                'licence': m1, 'filiere': programme, 'promotion': promo, 'cycle': 'Master'})

        self.stdout.write(self.style.SUCCESS(
            f"Programme MASTER EN ENERGIE {'créé' if cree else 'déjà présent'} ; niveaux MASTER 1 / MASTER 2 ; "
            f"promotion PM01 prévue en MASTER 1 pour {session}."))
        self.stdout.write(f"Compte : {IDENTIFIANT} (chef du programme, signe les documents de ce Master)")
        if mot_de_passe:
            self.stdout.write(self.style.WARNING(f"Mot de passe (affiché une seule fois, notez-le) : {mot_de_passe}"))
        else:
            self.stdout.write("Mot de passe inchangé (--nouveau-mot-de-passe pour en tirer un autre).")
        self.stdout.write(f"Étudiante de démonstration : Mariama DIALLO, préinscrite en MASTER 1 — onglet Master de "
                          f"/etudiant/ avec l'adresse {etudiant.email}")
        if not (session.inscriptions_ouvertes or session.reinscriptions_ouvertes):
            self.stdout.write(self.style.WARNING("Les inscriptions en ligne sont fermées : ouvrez-les dans « Demandes "
                                                 "en ligne » pour essayer l'onglet Master."))

    def supprimer(self):
        with transaction.atomic():
            n_etu = Etudiant.objects.filter(matricule=MATRICULE_ETUDIANT).delete()[0]
            user = User.objects.filter(username=IDENTIFIANT).first()
            if user is not None:
                Personnel.objects.filter(utilisateur=user).delete()
                user.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Compte {IDENTIFIANT} {'supprimé' if user else 'absent'} ; étudiante de démonstration "
            f"{'supprimée' if n_etu else 'absente'}. Le programme, les niveaux et la promotion restent."))
