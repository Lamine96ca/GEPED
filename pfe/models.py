import re

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.contrib.auth.models import User

# Cycles d'étude (communs aux services/programmes, inscriptions et attestations)
CYCLE_CHOICES = [
    ('DUT', 'DUT'),
    ('Tronc commun', 'Tronc commun'),
    ('Licence', 'Licence'),
    ('Ingénieur', 'Ingénieur'),
    ('Master', 'Master'),
    ('Doctorat', 'Doctorat'),
]


# Create your models here.
class Service(models.Model):
    nom = models.CharField(max_length=100, unique=True, verbose_name="Nom de la service")
    description = models.TextField(blank=True, null=True, verbose_name="Description de la service")
    programme = models.BooleanField(default=False, verbose_name="Programme : Oui/Non")
    code = models.CharField(max_length=5,default='0000')
    image = models.ImageField(upload_to="filiere/images", default="filiere/images/default.jpg", verbose_name="Image de la service")
    cycle = models.CharField(max_length=20, choices=CYCLE_CHOICES, verbose_name="Cycle", blank=True, null=True)
    # Département du programme (un autre Service, ou lui-même : Génie Informatique est à la fois
    # département et programme). Vide : le programme sert de département.
    departement = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, db_column='departement',
                                    related_name='programmes_rattaches', verbose_name="Département")

    def __str__(self):
        return self.nom

    @property
    def departement_effectif(self):
        """Le département du programme ; à défaut, le programme lui-même."""
        return self.departement or self
#PERSONNEL
class Personnel(models.Model):
    CIVILITE_CHOICES = [('M.', 'M.'), ('Mme', 'Mme'), ('Dr', 'Dr'), ('Pr', 'Pr')]
    # Devant le nom du signataire sur les documents (« M. Lanciné 2 CONDE »)
    civilite = models.CharField(max_length=5, choices=CIVILITE_CHOICES, blank=True, default='', verbose_name="Civilité")
    prenom = models.CharField(max_length=100, verbose_name="Prénom")
    nom = models.CharField(max_length=50, verbose_name="Nom")
    telephone = models.CharField(max_length=25, unique=True, verbose_name="Téléphone")
    email = models.CharField(max_length=150, unique=True, verbose_name="E-mail")
    fonc = models.CharField(max_length=100, verbose_name="Fonction")
    bio = models.CharField(max_length=200,blank=True,null=True, verbose_name="Biographie")
    service = models.ManyToManyField(Service, verbose_name="Service", related_name="personnel_service")
    photo = models.ImageField(upload_to="personnel/photo", default="", null=True, blank=True, verbose_name="Photo du personnel")

    utilisateur = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profil_personnel')
    # Chef du Service Scolarité, ou chef d'un département (programme qui est son propre département) :
    # seul un chef (ou un super utilisateur) modifie les notes d'une année écoulée. Coché par un super utilisateur.
    chef = models.BooleanField(default=False, verbose_name="Chef de service ou de département")

    def __str__(self):
        return f"{self.prenom} {self.nom}"

    @property
    def nom_signature(self):
        """Nom imprimé sous la signature : civilité, prénom, NOM (« M. Lanciné 2 CONDE »)."""
        return ' '.join(x for x in (self.civilite, self.prenom.strip(), self.nom.strip().upper()) if x)

#SESSION
class Session(models.Model):
    annee = models.CharField(max_length=20, unique=True, verbose_name="Année académique")
    en_cours = models.BooleanField(default=False, verbose_name="Session en cours")
    # Espace étudiant (sans compte) : ouverture des dépôts pour cette session, par la scolarité
    inscriptions_ouvertes = models.BooleanField(default=False, verbose_name="Inscriptions ouvertes (espace étudiant)")
    reinscriptions_ouvertes = models.BooleanField(default=False,
                                                  verbose_name="Réinscriptions ouvertes (espace étudiant)")

    def __str__(self):
        return self.annee

#LICENCE
class Licence(models.Model):
    nom = models.CharField(max_length=100, unique=True, verbose_name="Nom de la licence")
    description = models.TextField(blank=True, null=True, verbose_name="Description de la licence")

    def __str__(self):
        return self.nom

#PROMOTION
class Promotion(models.Model):
    nom = models.CharField(max_length=100, unique=True, verbose_name="Nom de la promotion")
    description = models.TextField(blank=True, null=True, verbose_name="Description de la promotion")
    
    def __str__(self):
        return self.nom

#SEMESTRE
class Semestre(models.Model):
    # Identifié partout par son id ; le nom n'est unique que dans son niveau (« SEMESTRE 1 » existe en
    # LICENCE 1 comme en MASTER 1)
    nom = models.CharField(max_length=100, verbose_name="Nom du semestre")
    abrege = models.CharField(max_length=5, default="")
    description = models.TextField(blank=True, null=True, verbose_name="Description du semestre")
    programme = models.BooleanField(default=False, verbose_name="Programme : Oui/Non")
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, related_name='licence_semestre', verbose_name="Licence")

    class Meta:
        constraints = [models.UniqueConstraint(fields=['nom', 'licence'], name='semestre_unique_par_niveau')]

    def __str__(self):
        return self.nom

#ETUDIANT  
class Etudiant(models.Model):
    matricule = models.CharField(max_length=25, unique=True, verbose_name="Matricule", blank=True, null=True)
    prenom = models.CharField(max_length=100, verbose_name="Prénom")
    nom = models.CharField(max_length=50, verbose_name="Nom")
    date_de_naissance = models.DateField(verbose_name="Date de naissance", default=None, blank=True, null=True)
    lieu_de_naissance = models.CharField(max_length=100, verbose_name="Lieu de naissance", blank=True, null=True)
    SEXE_CHOICES = [
        ('M', 'Masculin'),
        ('F', 'Féminin'),
    ]
    sexe = models.CharField( max_length=1,choices=SEXE_CHOICES,default='M',verbose_name="Sexe")
    pere = models.CharField(max_length=100, verbose_name="Nom du père", blank=True, null=True)
    mere = models.CharField(max_length=100, verbose_name="Nom de la mère", blank=True, null=True)
    nationalite = models.CharField(max_length=50,verbose_name="Nationalité", blank=True, null=True)
   
    photo = models.ImageField(upload_to="etudiant/photo", default="etudiant/photo/default.jpg", null=True, blank=True, verbose_name="Photo de l'étudiant")
    utilisateur = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profil_etudiant', blank=True, null=True)

    ine = models.CharField(max_length=25, verbose_name="INE", blank=True, null=True, default="")
    pvbac = models.CharField(max_length=25, verbose_name="PV Baccalauréat", blank=True, null=True)
    annee_obtention_bac = models.CharField(max_length=4, verbose_name="Année d'obtention bac", blank=True, null=True)
    annee_orientation = models.CharField(max_length=4, verbose_name="Année d'orientation", blank=True, null=True)
    option_de_bac = models.CharField(max_length=100, verbose_name="Option du Baccalauréat", blank=True, null=True)
    moyenne_au_bac = models.DecimalField(max_digits=4, decimal_places=2, verbose_name="Moyenne au Baccalauréat", blank=True, null=True)
    ecole_origine = models.CharField(max_length=150, verbose_name="École d'origine", blank=True, null=True)
    centre_de_bac = models.CharField(max_length=150, verbose_name="Centre du Baccalauréat", blank=True, null=True)
    ville = models.CharField(max_length=100,verbose_name="Ville/Commune",blank=True,null=True)

    telephone = models.CharField(max_length=25, verbose_name="Téléphone", blank=True, null=True, default="")
    email = models.CharField(max_length=150, unique=True, verbose_name="E-mail")

    tuteur = models.CharField(max_length=100, verbose_name="Nom du tuteur", blank=True, null=True)
    contact_de_tuteur = models.CharField(max_length=25, verbose_name="Contact du tuteur", blank=True, null=True)
    adresse = models.CharField(max_length=200, verbose_name="Adresse", blank=True, null=True)

    
    preinscrit = models.BooleanField(default=0,verbose_name="Préinscrit")

    # Cochés sur la fiche d'inscription (vides : cases laissées à cocher à la main)
    BOURSIER_CHOICES = [
        ('boursier', 'Boursier'),
        ('non_boursier', 'Non boursier'),
        ('payante', 'Formation payante'),
    ]
    TYPE_FORMATION_CHOICES = [
        ('initiale', 'Initiale'),
        ('continue', 'Continue'),
    ]
    boursier = models.CharField(max_length=20, choices=BOURSIER_CHOICES, blank=True, default='', verbose_name="Bourse")
    type_formation = models.CharField(max_length=20, choices=TYPE_FORMATION_CHOICES, blank=True, default='',
                                      verbose_name="Type de formation")

    # Situation de handicap (donnée sensible : affichée à la scolarité seulement, comptée dans les statistiques)
    TYPE_HANDICAP_CHOICES = [
        ('moteur', 'Moteur'),
        ('visuel', 'Visuel'),
        ('auditif', 'Auditif'),
        ('psychique', 'Psychique ou cognitif'),
        ('autre', 'Autre'),
    ]
    handicap = models.BooleanField(default=False, verbose_name="Situation de handicap")
    type_handicap = models.CharField(max_length=20, choices=TYPE_HANDICAP_CHOICES, blank=True, default='',
                                     verbose_name="Type de handicap")
    amenagements = models.CharField(max_length=255, blank=True, default='', verbose_name="Aménagements nécessaires")


    def __str__(self):
        return self.matricule or f"{self.prenom} {self.nom}"

#INSCRIPTION
class Inscription(models.Model):
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='etudiant_inscription', verbose_name="Étudiant")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_inscription', verbose_name="Session")
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, related_name='licence_inscription', verbose_name="Licence")
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE,null=True,blank=True)
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name='promotion_inscription', verbose_name="Promotion", blank=True, null=True)
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='filiere_etudiant', blank=True, null=True, verbose_name="Programme")
    date_inscription = models.DateField(auto_now_add=True, verbose_name="Date d'inscription")
    redouble = models.BooleanField(verbose_name="Redoublant", default=0)
    CYCLE_CHOICES = CYCLE_CHOICES
    cycle = models.CharField(max_length=20, choices=CYCLE_CHOICES, verbose_name="Cycle d'étude", blank=True, null=True)

    class Meta:
        unique_together = ('etudiant', 'session', 'licence',)

    def __str__(self):
        return f"Inscription de {self.etudiant} en {self.licence} - {self.semestre} pour la session {self.session}"
    
#PROMOTION_LICENCE_SESSION
class Promotion_Licence_Session(models.Model):
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name='promotion_licence_session', verbose_name="Promotion")
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, related_name='licence_promotion_session', verbose_name="Licence")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_promotion_licence', verbose_name="Session")

    class Meta:
        unique_together = ('promotion', 'licence', 'session')
        verbose_name = "Niveau d'une promotion (promotion · licence · session)"
        verbose_name_plural = "Niveaux des promotions (promotion · licence · session)"

    def __str__(self):
        return f"{self.promotion} · {self.licence} · {self.session}"

#ATTESTATION
def nouveau_code_verification():
    """22 caractères aléatoires (128 bits) pour l'URL publique de vérification d'un document."""
    import secrets
    return secrets.token_urlsafe(16)


class CompteurAttestation(models.Model):
    """Dernier n° d'attestation attribué pour une année (numérotation continue, jamais réutilisée)."""
    annee = models.PositiveIntegerField(unique=True, verbose_name="Année")
    dernier = models.PositiveIntegerField(default=0, verbose_name="Dernier numéro attribué")

    def __str__(self):
        return f"{self.annee} : {self.dernier}"


class Attestation(models.Model):
    num_ref = models.CharField(max_length=50, verbose_name="N° de référence")
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='etudiant_attestation', verbose_name="Matricule" , blank=True, null=True)
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, related_name='licence_attestation', verbose_name="Licence" , blank=True, null=True)
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, verbose_name="Programme" , blank=True, null=True)
    session = models.ForeignKey(Session, on_delete=models.CASCADE, verbose_name="Session", related_name='Session_session', blank=True, null=True)
    session_actuelle = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_attestation', verbose_name='Session actuelle', blank=True, null=True)
    ORIENTATION, INSCRIPTION, NIVEAU, FIN_ETUDES, SOUTENANCE = '1', '2', '3', '4', '5'
    type_attestation = [
        (ORIENTATION, 'Orientation'),
        (INSCRIPTION, 'Inscription'),
        (NIVEAU, 'Niveau'),
        (FIN_ETUDES, "Fin d'études"),
        (SOUTENANCE, 'Soutenance'),            # cycle = celui de la soutenance (une par étudiant et par cycle)
    ]
    type = models.CharField(max_length=1, choices=type_attestation, verbose_name="Type d'attestation", blank=True, null=True)
    date_delivrance = models.DateField(auto_now_add=True)
    # Cycle attesté (attestation de fin d'études : « Premier cycle LMD », etc.)
    cycle = models.CharField(max_length=20, choices=Inscription.CYCLE_CHOICES, blank=True, null=True,
                             verbose_name="Cycle")
    # Code du QR de vérification (page publique). Aléatoire : impossible de deviner celui d'un autre document.
    code_verification = models.CharField(max_length=32, unique=True, editable=False,
                                         default=nouveau_code_verification, verbose_name="Code de vérification")

    # Suffixe du n° selon le type (l'orientation est en DG, les autres en SC, ou en SEA pour le Master).
    # Le NUMÉRO, lui, est continu pour tous : 0139/ISTM/SC/26, 0140/ISTM/DG/26, 0141/ISTM/SEA/26, etc.
    SUFFIXES_REF = {ORIENTATION: '/ISTM/DG/'}
    SUFFIXE_REF = '/ISTM/SC/'
    SUFFIXE_MASTER = '/ISTM/SEA/'

    @classmethod
    def suffixe_pour(cls, type_attestation, annee, master=False):
        defaut = cls.SUFFIXE_MASTER if master else cls.SUFFIXE_REF
        return f"{cls.SUFFIXES_REF.get(type_attestation, defaut)}{str(annee)[-2:]}"

    @classmethod
    def _dernier_numero(cls, annee):
        """Plus grand numéro attribué cette année, tous types et suffixes : compteur ou attestations existantes."""
        motif = re.compile(rf"^(\d+)/ISTM/[A-Z]+/{str(annee)[-2:]}$")
        numeros = [int(m.group(1)) for ref in
                   cls.objects.filter(num_ref__endswith=f"/{str(annee)[-2:]}").values_list('num_ref', flat=True)
                   if (m := motif.match(ref))]
        compteur = CompteurAttestation.objects.filter(annee=annee).values_list('dernier', flat=True).first() or 0
        return max(numeros + [compteur])

    @classmethod
    def prochaine_reference(cls, type_attestation=None, annee=None, master=False):
        """
        N° suivant, CONTINU pour tous les types d'attestation (orientation, inscription,
        niveau, fin d'études) et repris à 0001 chaque année : 0139/ISTM/SC/26 (SEA en Master).
        Un compteur par année garde le dernier numéro attribué : même après la suppression
        d'une attestation, son numéro n'est jamais réattribué.
        """
        annee = annee or timezone.localdate().year
        return f"{cls._dernier_numero(annee) + 1:04d}{cls.suffixe_pour(type_attestation, annee, master)}"

    def save(self, *args, **kwargs):
        if not self.num_ref:
            from django.db import transaction
            from .signataires import est_master
            annee = timezone.localdate().year
            master = est_master(self.cycle, self.licence, self.filiere)
            with transaction.atomic():
                compteur, _ = CompteurAttestation.objects.select_for_update().get_or_create(annee=annee)
                compteur.dernier = Attestation._dernier_numero(annee) + 1
                compteur.save(update_fields=['dernier'])
                self.num_ref = f"{compteur.dernier:04d}{Attestation.suffixe_pour(self.type, annee, master)}"
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)


    def __str__(self):
        return self.num_ref

#MATIERE
class Matiere(models.Model):
    nom = models.CharField(max_length=100, verbose_name="Matière")
    code = models.CharField(max_length=10, verbose_name="Code", blank=True,null=True)
    credit = models.IntegerField(default=0)
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE,verbose_name="Semestre")
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, verbose_name="Licence")
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, verbose_name="Programme", blank=True, null=True)
    session = models.ForeignKey(Session, on_delete=models.CASCADE, verbose_name="Session")
    description = models.TextField(verbose_name="Description",blank=True,null=True)
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, blank=True, null=True)
    # Cycle de la matière (vide à la saisie ou à l'import → cycle du programme)
    cycle = models.CharField(max_length=20, choices=CYCLE_CHOICES, verbose_name="Cycle", blank=True, null=True)

    class Meta:
        unique_together = ('nom','filiere','session')

    def __str__(self):
        return self.nom
    
#NOTES
# Barème et règles de calcul : settings.GPED_NOTES (notes sur 10, matière validée à partir de 5).
# Les calculs eux-mêmes sont dans pfe/notes.py.

def stockage_prive():
    """Justificatifs : dossier NON servi par le site (documents internes)."""
    from django.conf import settings
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage(location=settings.BASE_DIR / 'prive')


NOTE_SUR_10 = [MinValueValidator(0), MaxValueValidator(10)]


class Note(models.Model):
    """
    Note d'un étudiant dans une matière. La matière porte déjà la session, le programme, la
    licence, le semestre et les crédits : rien n'est recopié ici.

    - moyenne : déposée par le programme (moyenne_deposee) ou calculée à partir des notes
      données : contrôle 25 % + TP 35 % + examen 40 % ; sans TP : contrôle 40 % + examen 60 % ;
    - rattrapage : note de la session de rattrapage ; on garde la meilleure des deux, avec la
      mention « Session » (par_session) si c'est le rattrapage qui est retenu ;
    - un absent a 0.
    """
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='notes', verbose_name='Étudiant')
    matiere = models.ForeignKey(Matiere, on_delete=models.PROTECT, related_name='notes', verbose_name='Matière')
    controle = models.FloatField('Contrôle', null=True, blank=True, validators=NOTE_SUR_10)
    tp = models.FloatField('TP', null=True, blank=True, validators=NOTE_SUR_10)
    examen = models.FloatField('Examen', null=True, blank=True, validators=NOTE_SUR_10)
    moyenne = models.FloatField('Moyenne', null=True, blank=True, validators=NOTE_SUR_10)
    moyenne_deposee = models.BooleanField('Moyenne déposée', default=False,
                                          help_text="Moyenne donnée directement par le programme (pas recalculée).")
    rattrapage = models.FloatField('Rattrapage', null=True, blank=True, validators=NOTE_SUR_10)
    moyenne_finale = models.FloatField('Moyenne finale', null=True, blank=True, editable=False)
    par_session = models.BooleanField('Validée en session', default=False, editable=False)
    date_maj = models.DateTimeField('Dernière modification', auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['etudiant', 'matiere'], name='note_unique_par_matiere')]
        ordering = ['etudiant', 'matiere']

    def __str__(self):
        return f"{self.etudiant} · {self.matiere} : {self.moyenne_finale if self.moyenne_finale is not None else '—'}"

    def calculer(self):
        """Moyenne (si elle n'est pas déposée) puis moyenne finale = meilleure de la moyenne et du rattrapage."""
        from .notes import arrondi, moyenne_des_notes
        if not self.moyenne_deposee:
            self.moyenne = moyenne_des_notes(self.controle, self.tp, self.examen)
        self.par_session = self.rattrapage is not None and (self.moyenne is None or self.rattrapage > self.moyenne)
        meilleure = self.rattrapage if self.par_session else self.moyenne
        self.moyenne_finale = arrondi(meilleure)

    def save(self, *args, **kwargs):
        self.calculer()
        super().save(*args, **kwargs)

    @property
    def validee(self):
        from .notes import est_validee
        return est_validee(self.moyenne_finale)

    @property
    def mention(self):
        from .notes import mention
        return mention(self.moyenne_finale)

    @property
    def verrouillee(self):
        """Semestre délibéré : seule la scolarité peut encore modifier la note (avec justificatif)."""
        m = self.matiere
        return Deliberation.objects.filter(filiere_id=m.filiere_id, session_id=m.session_id,
                                           semestre_id=m.semestre_id).exists()


class Deliberation(models.Model):
    """Délibération d'un semestre pour un programme et une session : les notes sont alors verrouillées."""
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='deliberations', verbose_name='Programme')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, verbose_name='Session')
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE, verbose_name='Semestre')
    date = models.DateTimeField('Date de délibération', auto_now_add=True)
    par = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Par')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['filiere', 'session', 'semestre'], name='deliberation_unique')]

    def __str__(self):
        return f"Délibération {self.filiere} · {self.semestre} · {self.session}"


class NoteHistorique(models.Model):
    """Trace de chaque modification d'une note : avant / après, auteur, date, motif et justificatif."""
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name='historique')
    date = models.DateTimeField(auto_now_add=True)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Modifiée par')
    avant = models.JSONField('Avant', default=dict, blank=True)
    apres = models.JSONField('Après', default=dict)
    apres_deliberation = models.BooleanField('Après délibération', default=False)
    motif = models.TextField('Motif', blank=True)
    justificatif = models.FileField('Justificatif', upload_to='justificatifs/notes/%Y', storage=stockage_prive,
                                    blank=True)

    class Meta:
        ordering = ['-date']


class PieceDossier(models.Model):
    """
    Pièce du dossier d'un étudiant (acte de naissance, diplôme et relevé du bac, relevé de notes…),
    toujours rangée en PDF compressé dans le stockage privé (jamais publiée). Déposée par l'étudiant
    depuis l'espace d'inscription, ou par la scolarité depuis la fiche.
    """
    ACTE_NAISSANCE, DIPLOME_BAC, RELEVE_BAC, RELEVE_NOTES, DIPLOME_LICENCE, AUTRE = (
        'acte_naissance', 'diplome_bac', 'releve_bac', 'releve_notes', 'diplome_licence', 'autre')
    TYPES = [
        (ACTE_NAISSANCE, 'Acte de naissance'),
        (DIPLOME_BAC, 'Diplôme du baccalauréat'),
        (RELEVE_BAC, 'Relevé de notes du baccalauréat'),
        (RELEVE_NOTES, "Relevé de notes de la dernière année"),
        (DIPLOME_LICENCE, 'Diplôme de licence'),
        (AUTRE, 'Autre pièce'),
    ]
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='pieces', verbose_name='Étudiant')
    type = models.CharField('Pièce', max_length=20, choices=TYPES)
    libelle = models.CharField('Précision', max_length=120, blank=True)
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Session')
    fichier = models.FileField('Fichier (PDF)', upload_to='dossiers/%Y', storage=stockage_prive)
    pages = models.PositiveSmallIntegerField('Pages', default=1)
    taille = models.PositiveIntegerField('Taille (octets)', default=0)
    par_etudiant = models.BooleanField("Déposée par l'étudiant", default=False)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Déposée par')
    date = models.DateTimeField('Date', auto_now_add=True)

    class Meta:
        ordering = ['type', '-date']

    def __str__(self):
        return f"{self.get_type_display()} · {self.etudiant}"


class DemandeEtudiant(models.Model):
    """
    Demande déposée par l'étudiant depuis l'espace étudiant (sans compte) :
      - inscription (préinscrit, identifié par INE + PV du bac) ;
      - réinscription (identifié par INE + matricule), avec le niveau demandé.
    Les contacts, la bourse, le type de formation et la photo proposés restent EN ATTENTE : ils ne
    sont appliqués à la fiche qu'à la validation par la scolarité (« Inscrire » pour un préinscrit,
    « Valider » pour une réinscription, qui crée alors l'inscription de la session).
    Les pièces scannées vont directement dans le dossier (PieceDossier).
    """
    INSCRIPTION, REINSCRIPTION = 'inscription', 'reinscription'
    TYPES = [(INSCRIPTION, 'Inscription'), (REINSCRIPTION, 'Réinscription')]
    EN_ATTENTE, VALIDEE, REFUSEE = 'en_attente', 'validee', 'refusee'
    STATUTS = [(EN_ATTENTE, 'En attente'), (VALIDEE, 'Validée'), (REFUSEE, 'Refusée')]
    CHAMPS_CONTACTS = ['telephone', 'email', 'adresse', 'tuteur', 'contact_de_tuteur']   # la ville n'est pas demandée

    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='demandes', verbose_name='Étudiant')
    type = models.CharField('Demande', max_length=15, choices=TYPES)
    session = models.ForeignKey(Session, on_delete=models.CASCADE, verbose_name='Session')
    statut = models.CharField('Statut', max_length=12, choices=STATUTS, default=EN_ATTENTE)
    contacts = models.JSONField('Contacts proposés', default=dict, blank=True)
    # Boursier et formation initiale par défaut ; la scolarité peut changer avant de valider
    boursier = models.CharField('Bourse', max_length=20, choices=Etudiant.BOURSIER_CHOICES, blank=True,
                                default='boursier')
    type_formation = models.CharField('Type de formation', max_length=20, choices=Etudiant.TYPE_FORMATION_CHOICES,
                                      blank=True, default='initiale')
    licence = models.ForeignKey(Licence, on_delete=models.SET_NULL, null=True, blank=True,
                                verbose_name='Niveau demandé')
    derniere_inscription = models.ForeignKey(Inscription, on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='+', verbose_name='Dernière inscription')
    photo = models.FileField('Nouvelle photo proposée', upload_to='demandes/photos/%Y', storage=stockage_prive,
                             blank=True)
    date_depot = models.DateTimeField('Déposée le', auto_now_add=True)
    date_maj = models.DateTimeField('Mise à jour le', auto_now=True)
    traitee_le = models.DateTimeField('Traitée le', null=True, blank=True)
    traitee_par = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                    verbose_name='Traitée par')
    motif_refus = models.TextField('Motif du refus', blank=True)
    # Signature électronique simple de l'étudiant (dessinée), obligatoire à chaque envoi : image, date,
    # appareil et empreinte des informations signées ; reprise sur la fiche d'inscription
    signature = models.FileField('Signature', upload_to='demandes/signatures/%Y', storage=stockage_prive, blank=True)
    signee_le = models.DateTimeField('Signée le', null=True, blank=True)
    signature_ip = models.CharField('Adresse IP', max_length=45, blank=True)
    signature_appareil = models.CharField('Appareil', max_length=255, blank=True)
    empreinte = models.CharField('Empreinte des informations signées', max_length=64, blank=True)

    class Meta:
        ordering = ['-date_maj']
        constraints = [models.UniqueConstraint(fields=['etudiant', 'session', 'type'], name='demande_unique')]

    def __str__(self):
        return f"{self.get_type_display()} {self.session} · {self.etudiant}"


class ImportNotes(models.Model):
    """
    Journal des imports de notes : qui, quand, quel fichier, et le PDF de l'enseignant (fiche de notes
    signée), obligatoire pour un département ou un programme. Il sert aussi de justificatif aux notes
    modifiées par cet import.
    """
    date = models.DateTimeField(auto_now_add=True)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Importé par')
    fichier = models.CharField('Fichier importé', max_length=255, blank=True)
    pdf_enseignant = models.FileField("PDF de l'enseignant", upload_to='justificatifs/imports_notes/%Y',
                                      storage=stockage_prive, blank=True)
    creees = models.PositiveIntegerField('Notes créées', default=0)
    modifiees = models.PositiveIntegerField('Notes modifiées', default=0)
    programmes = models.CharField('Programmes', max_length=255, blank=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"Import {self.fichier} · {self.date:%d/%m/%Y %H:%M}"


class Resultat(models.Model):
    """
    Résultats calculés (pfe/notes.py, jamais saisis) : semestre, année (licence) ou cycle.
    Moyennes pondérées par les crédits ; une matière sans note compte 0.
    Cycle : moyenne des années du cycle + soutenance → moyenne générale.
    """
    SEMESTRE, ANNEE, CYCLE = 'S', 'A', 'C'
    NIVEAUX = [(SEMESTRE, 'Semestre'), (ANNEE, 'Année'), (CYCLE, 'Cycle')]

    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='resultats')
    niveau = models.CharField(max_length=1, choices=NIVEAUX)
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, null=True, blank=True, verbose_name='Programme')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, null=True, blank=True,
                                help_text="Pour un cycle : la session de la dernière année.")
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, null=True, blank=True)
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE, null=True, blank=True)
    cycle = models.CharField(max_length=20, choices=CYCLE_CHOICES, null=True, blank=True)
    moyenne = models.FloatField(null=True, blank=True)
    credits_valides = models.PositiveIntegerField(default=0)
    credits_total = models.PositiveIntegerField(default=0)
    notes_manquantes = models.PositiveIntegerField(default=0)
    soutenance = models.FloatField(null=True, blank=True)            # cycle uniquement
    moyenne_generale = models.FloatField(null=True, blank=True)      # cycle : cycle × 0,87 + soutenance × 0,13
    mention = models.CharField(max_length=20, blank=True)
    rang = models.PositiveIntegerField(null=True, blank=True)
    date_calcul = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['niveau', 'filiere', 'session'])]

    def __str__(self):
        return f"{self.get_niveau_display()} {self.etudiant} : {self.moyenne}"


class ProjetSoutenance(models.Model):
    """
    Projet de soutenance : 1 à 5 étudiants ou plus (les membres sont leurs Soutenance).
    Tout est en texte libre et facultatif, pour pouvoir reprendre les anciens étudiants.
    L'encadreur et le chef de département signent le PV.
    """
    PROGRAMMEE, SOUTENUE, REPORTEE, AJOURNEE = 'programmee', 'soutenue', 'reportee', 'ajournee'
    STATUTS = [(PROGRAMMEE, 'Programmée'), (SOUTENUE, 'Soutenue'), (REPORTEE, 'Reportée'), (AJOURNEE, 'Ajournée')]

    intitule = models.CharField('Projet', max_length=120, help_text="Nom ou numéro du projet, ex. « Projet 12 ».")
    theme = models.TextField('Thème', blank=True)
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='projets_soutenance',
                                verbose_name='Programme')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='projets_soutenance',
                                verbose_name='Session')
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name='projets_soutenance',
                                  verbose_name='Promotion')
    cycle = models.CharField('Cycle', max_length=20, choices=CYCLE_CHOICES, blank=True)
    encadreur = models.CharField('Encadreur', max_length=150, blank=True)
    maitre_stage = models.CharField('Maître de stage', max_length=150, blank=True)
    entreprise = models.CharField('Entreprise / structure d’accueil', max_length=150, blank=True)
    chef_departement = models.CharField('Chef de département', max_length=150, blank=True)
    president_jury = models.CharField('Président du jury', max_length=150, blank=True)
    examinateur = models.CharField('Examinateur', max_length=150, blank=True)
    rapporteur = models.CharField('Rapporteur', max_length=150, blank=True)
    date = models.DateField('Date de soutenance', null=True, blank=True)
    heure = models.TimeField('Heure', null=True, blank=True)
    salle = models.CharField('Salle', max_length=60, blank=True)
    statut = models.CharField('Statut', max_length=12, choices=STATUTS, default=PROGRAMMEE)

    class Meta:
        verbose_name = 'projet de soutenance'
        verbose_name_plural = 'projets de soutenance'
        constraints = [models.UniqueConstraint(fields=['intitule', 'filiere', 'session', 'promotion'],
                                               name='projet_soutenance_unique')]

    def __str__(self):
        return self.intitule

    @property
    def jury(self):
        return [(titre, nom) for titre, nom in (('Président', self.president_jury), ('Examinateur', self.examinateur),
                                               ('Rapporteur', self.rapporteur)) if nom]


class Soutenance(models.Model):
    """
    Soutenance d'un étudiant (une par cycle), notée sur 10. Une seconde note (« note de session »)
    peut être donnée s'il la repasse : on garde la meilleure, avec la mention « Session ».
    La note retenue entre dans la moyenne générale du cycle. Notes verrouillées : par le bouton
    « Verrouiller » ou d'office à l'import ; ensuite seule la scolarité les modifie (avec motif).
    """
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='soutenances', verbose_name='Étudiant')
    cycle = models.CharField(max_length=20, choices=CYCLE_CHOICES, verbose_name='Cycle')
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, verbose_name='Programme')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, verbose_name='Session')
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, verbose_name='Promotion')
    projet = models.ForeignKey(ProjetSoutenance, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='membres', verbose_name='Projet de soutenance')
    note = models.FloatField('Note de soutenance', null=True, blank=True, validators=NOTE_SUR_10)
    note_session = models.FloatField('Note de session', null=True, blank=True, validators=NOTE_SUR_10)
    note_finale = models.FloatField('Note retenue', null=True, blank=True, editable=False)
    par_session = models.BooleanField('Validée en session', default=False, editable=False)
    verrouillee = models.BooleanField('Note verrouillée', default=False)
    verrouillee_le = models.DateTimeField('Verrouillée le', null=True, blank=True)
    verrouillee_par = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                        verbose_name='Verrouillée par')
    date_maj = models.DateTimeField('Dernière modification', auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['etudiant', 'cycle'], name='soutenance_unique_par_cycle')]

    def __str__(self):
        return f"Soutenance de {self.etudiant} ({self.get_cycle_display()})"

    def calculer(self):
        from .notes import arrondi
        self.par_session = self.note_session is not None and (self.note is None or self.note_session > self.note)
        self.note_finale = arrondi(self.note_session if self.par_session else self.note)

    def save(self, *args, **kwargs):
        self.calculer()
        super().save(*args, **kwargs)

    @property
    def validee(self):
        from .notes import est_validee
        return est_validee(self.note_finale)

    @property
    def mention(self):
        from .notes import mention
        return mention(self.note_finale)


class SoutenanceHistorique(models.Model):
    """Trace de chaque modification d'une note de soutenance (avant / après, auteur, motif, justificatif)."""
    soutenance = models.ForeignKey(Soutenance, on_delete=models.CASCADE, related_name='historique')
    date = models.DateTimeField(auto_now_add=True)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Modifiée par')
    avant = models.JSONField('Avant', default=dict, blank=True)
    apres = models.JSONField('Après', default=dict)
    note_verrouillee = models.BooleanField('Note verrouillée', default=False)
    motif = models.TextField('Motif', blank=True)
    justificatif = models.FileField('Justificatif', upload_to='justificatifs/soutenances/%Y', storage=stockage_prive,
                                    blank=True)

    class Meta:
        ordering = ['-date']

#RELEVÉS DE NOTES
from django.utils import timezone


class CompteurReleve(models.Model):
    """Dernier n° de relevé attribué pour une année : compteur PROPRE aux relevés (pas celui des attestations)."""
    annee = models.PositiveIntegerField(unique=True, verbose_name="Année")
    dernier = models.PositiveIntegerField(default=0, verbose_name="Dernier numéro attribué")

    def __str__(self):
        return f"{self.annee} : {self.dernier}"


class ReleveNote(models.Model):
    """
    Relevé délivré (le contenu est recalculé à chaque impression, d'après les notes) :
      - semestre : matières d'un semestre, dans le programme, la session et la licence d'une inscription ;
      - annuel   : toutes les matières de la licence de l'inscription ;
      - extrait  : tout le parcours de l'étudiant dans UN programme (le Tronc commun a son propre extrait).
    N° 0001/ISTM/SC/26 (SEA en Master), continu sur l'année pour tous les relevés, repris à 0001 chaque année.
    """
    SEMESTRE, ANNUEL, EXTRAIT = 'S', 'A', 'E'
    TYPES = [(SEMESTRE, 'Relevé semestriel'), (ANNUEL, 'Relevé annuel'), (EXTRAIT, 'Extrait du livret')]

    num_ref = models.CharField('N° de référence', max_length=50, unique=True, editable=False)
    type = models.CharField('Type', max_length=1, choices=TYPES)
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='releves', verbose_name='Étudiant')
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE, verbose_name='Programme')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, null=True, blank=True, verbose_name='Session')
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, null=True, blank=True, verbose_name='Licence')
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE, null=True, blank=True, verbose_name='Semestre')
    date_delivrance = models.DateField('Délivré le', auto_now_add=True)
    code_verification = models.CharField(max_length=32, unique=True, editable=False,
                                         default=nouveau_code_verification, verbose_name='Code de vérification')

    class Meta:
        ordering = ['-date_delivrance', '-id']

    def __str__(self):
        return self.num_ref

    @classmethod
    def suffixe_pour(cls, annee, master=False):
        return f"{Attestation.SUFFIXE_MASTER if master else Attestation.SUFFIXE_REF}{str(annee)[-2:]}"

    @classmethod
    def prochaine_reference(cls, annee=None, master=False):
        annee = annee or timezone.localdate().year
        dernier = CompteurReleve.objects.filter(annee=annee).values_list('dernier', flat=True).first() or 0
        return f"{dernier + 1:04d}{cls.suffixe_pour(annee, master)}"

    def save(self, *args, **kwargs):
        if self.num_ref:
            return super().save(*args, **kwargs)
        from django.db import transaction
        from .signataires import releve_est_master
        annee = timezone.localdate().year
        master = releve_est_master(self)
        with transaction.atomic():
            compteur, _ = CompteurReleve.objects.select_for_update().get_or_create(annee=annee)
            compteur.dernier += 1
            compteur.save(update_fields=['dernier'])
            self.num_ref = f"{compteur.dernier:04d}{ReleveNote.suffixe_pour(annee, master)}"
            super().save(*args, **kwargs)

#ORIENTATION
class CritereAdmission(models.Model):
    filiere = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="criteres"
    )
    matiere = models.ForeignKey(
        Matiere,
        on_delete=models.CASCADE
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    note_min = models.FloatField()
    # option avancée (tu vas en avoir besoin)
    coefficient = models.FloatField(default=1)

    class Meta:
        unique_together = ('filiere', 'matiere', 'promotion')

    def __str__(self):
        return f"{self.filiere.nom} - {self.matiere.nom} ≥ {self.note_min}"

class CritereAdmissionMoyenne(models.Model):

    filiere = models.ForeignKey(
        Service,
        on_delete=models.CASCADE
    )

    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE
    )

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )

    moyenne_generale = models.FloatField()
    nombre_place = models.PositiveIntegerField(default=0)


    class Meta:
        unique_together = ('filiere', 'promotion')

    def __str__(self):

        return (
            f"{self.filiere.nom} "
            f"- {self.promotion.nom} "
            f"- Moyenne ≥ {self.moyenne_generale}"
        )

class Choix(models.Model):

    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE)
    filiere = models.ForeignKey(Service, on_delete=models.CASCADE)
    choix = models.IntegerField()
    compatibilite = models.IntegerField(default=0)
    statut = models.CharField(max_length=30,
        choices=[
            ('attente','En attente'),
            ('valide','Validé'),
            ('refuse','Refusé')
        ],
        default='attente'
    )
    moyenne = models.FloatField( null=True, blank=True)
    date_choix = models.DateTimeField(auto_now_add=True)
    session = models.ForeignKey(Session,on_delete=models.CASCADE,null=True,blank=True)
    promotion = models.ForeignKey( Promotion, on_delete=models.CASCADE, null=True, blank=True)
    valide = models.BooleanField(
        default=False
    )
    date_validation = models.DateTimeField(null=True, blank=True)

    class Meta:

        unique_together = (
            'etudiant',
            'choix',
            'session'
        )

        ordering = ['choix']

    def __str__(self):

        return f"""
        {self.etudiant}
        - Choix {self.choix}
        - {self.filiere}
        """

class ChoixOrientation(models.Model):

    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE)
    filiere = models.ForeignKey(Service,on_delete=models.CASCADE)
    ordre = models.PositiveIntegerField()
    compatibilite = models.FloatField(default=0)
    session = models.ForeignKey(Session, on_delete=models.CASCADE, blank=True, null=True)
    promotion = models.ForeignKey( Promotion, on_delete=models.CASCADE, blank=True, null=True)
    valide = models.BooleanField(default=False)
    date_choix = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (
            'etudiant',
            'ordre',
            'session'
        )
        ordering = ['ordre']

    def __str__(self):
        return (
            f"{self.etudiant} "
            f"- Choix {self.ordre} "
            f"- {self.filiere.nom}"
        )

class Notification(models.Model):

    TYPE_CHOIX = 'choix'
    TYPE_ORIENTATION = 'orientation'
    TYPE_RESULTAT = 'resultat'
    TYPE_INFO = 'info'

    TYPE_CHOICES = (
        (TYPE_CHOIX, 'Choix'),
        (TYPE_ORIENTATION, 'Orientation'),
        (TYPE_RESULTAT, 'Résultat'),
        (TYPE_INFO, 'Information'),
    )

    etudiant = models.ForeignKey(
        Etudiant,
        on_delete=models.CASCADE
    )

    titre = models.CharField(max_length=255)

    message = models.TextField()

    type_notification = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_INFO
    )

    lu = models.BooleanField(default=False)

    date_creation = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ['-date_creation']

    def __str__(self):
        return self.titre


class OrientationFinale(models.Model):

    etudiant = models.ForeignKey(
        Etudiant,
        on_delete=models.CASCADE
    )
    filiere = models.ForeignKey(
        Service,
        on_delete=models.CASCADE
    )
    choix = models.ForeignKey(
        ChoixOrientation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    moyenne = models.FloatField(
        default=0
    )
    compatibilite = models.FloatField(
        default=0
    )
    rang = models.IntegerField(
        default=0
    )

    date_orientation = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        unique_together = ( 'etudiant','session')

    def __str__(self):

        return (
            f"{self.etudiant} "
            f"-> {self.filiere.nom}"
        )


class ReglageAffichage(models.Model):
    """
    Réglages d'affichage communs à tous les comptes, modifiables seulement dans l'espace admin.
    Une seule ligne ; tant qu'elle n'existe pas, les valeurs par défaut s'appliquent.
    """
    nb_releves = models.PositiveSmallIntegerField(
        'Relevés délivrés affichés', default=50, validators=[MinValueValidator(1), MaxValueValidator(500)],
        help_text="Nombre de relevés (les plus récents) affichés dans la liste « Relevés délivrés » "
                  "pour tout le monde. Les relevés délivrés ensuite s'y ajoutent (30 + 1 nouveau = 31). "
                  "Les autres ne sont trouvés que par la recherche.")
    # Dernier relevé existant quand le nombre a été fixé : ceux d'après s'ajoutent à la liste
    dernier_releve = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Réglage d'affichage"
        verbose_name_plural = "Réglages d'affichage"

    def __str__(self):
        return "Réglages d'affichage"

    def save(self, *args, **kwargs):
        # Nouveau nombre : le décompte repart d'ici (réenregistrer le même nombre ne change rien)
        avant = ReglageAffichage.objects.filter(pk=self.pk).values_list('nb_releves', flat=True).first()
        if self.pk is None or avant != self.nb_releves or self.dernier_releve is None:
            self.dernier_releve = ReleveNote.objects.aggregate(m=models.Max('pk'))['m'] or 0
        super().save(*args, **kwargs)

    @classmethod
    def actuel(cls):
        return cls.objects.first() or cls()

    def releves_affiches(self, releves):
        """Les `nb_releves` derniers au moment du réglage, plus tous ceux délivrés depuis."""
        if self.dernier_releve is None:                       # rien d'enregistré : les N derniers
            return list(releves[:self.nb_releves])
        anciens = list(releves.filter(pk__lte=self.dernier_releve).values_list('pk', flat=True)[:self.nb_releves])
        return list(releves.filter(models.Q(pk__gt=self.dernier_releve) | models.Q(pk__in=anciens)))

