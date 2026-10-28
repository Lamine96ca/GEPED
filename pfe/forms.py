import re

from django import forms
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.db.models import Q

from django.core.exceptions import ValidationError

from .models import (Attestation, Etudiant, Inscription, Licence, Matiere, Personnel, Promotion, Semestre, Service,
                     ProjetSoutenance, Session, Soutenance)
from .photos import MESSAGE_ILLISIBLE, a_une_photo, compresser, enregistrer_photo, retirer_photo


class BootstrapMixin:
    """Ajoute les classes Bootstrap aux widgets."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in self.fields.values():
            w = champ.widget
            if isinstance(champ, forms.ModelChoiceField):
                champ.empty_label = 'Choisir…' if champ.required else '— Aucun —'
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault('class', 'form-check-input')
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault('class', 'form-select')
            else:
                w.attrs.setdefault('class', 'form-control')


class EtudiantForm(BootstrapMixin, forms.ModelForm):
    retirer_photo = forms.BooleanField(label="Retirer la photo", required=False)

    class Meta:
        model = Etudiant
        exclude = ['utilisateur']
        widgets = {
            'date_de_naissance': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            # Caché : rempli par la fenêtre photo (prise de vue / chargement + recadrage + compression)
            'photo': forms.FileInput(attrs={'accept': 'image/*', 'class': 'visually-hidden'}),
            # Boutons à cocher (une seule option), repris cochés sur la fiche d'inscription
            'boursier': forms.RadioSelect(attrs={'class': 'btn-check'}),
            'type_formation': forms.RadioSelect(attrs={'class': 'btn-check'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # default=0 sur le modèle : la case serait cochée par défaut
        self.initial['preinscrit'] = bool(self.instance.preinscrit)
        # Inscription en ligne validée : signalée à côté de la case « Préinscrit », qui reste modifiable
        from .models import DemandeEtudiant
        self.inscription_en_ligne = (DemandeEtudiant.objects.filter(
            etudiant_id=self.instance.pk, type=DemandeEtudiant.INSCRIPTION, statut=DemandeEtudiant.VALIDEE)
            .order_by('-traitee_le').first() if self.instance.pk else None)
        self.photo_actuelle = self.instance.photo if a_une_photo(self.instance) else None
        self._photo_initiale = self.instance.photo.name if self.instance.photo else None
        self._photo_compressee = None
        self.fields['photo'].error_messages['invalid_image'] = MESSAGE_ILLISIBLE
        self.fields['type_handicap'].choices = [('', '— Choisir —')] + Etudiant.TYPE_HANDICAP_CHOICES
        self.fields['boursier'].choices = Etudiant.BOURSIER_CHOICES                    # sans l'option vide « --- »
        self.fields['type_formation'].choices = Etudiant.TYPE_FORMATION_CHOICES
        self.fields['amenagements'].widget.attrs['placeholder'] = "Ex. tiers-temps aux examens, salle au rez-de-chaussée"

    def clean(self):
        donnees = super().clean()
        if not donnees.get('handicap'):          # « Non » : le type et les aménagements n'ont plus de sens
            donnees['type_handicap'], donnees['amenagements'] = '', ''
        return donnees

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and hasattr(photo, 'content_type'):          # nouveau fichier envoyé : compressé ici
            try:
                self._photo_compressee = compresser(photo)
            except ValidationError as e:
                raise forms.ValidationError(e.messages)
        return photo

    def save(self, commit=True):
        etudiant = super().save(commit=False)
        # Le fichier brut n'est jamais enregistré : la photo compressée l'est après l'étudiant.
        # On reprend la photo ACTUELLE en base (elle a pu être changée ou retirée depuis la fiche
        # pendant que le formulaire était ouvert) : le formulaire ne remet jamais une ancienne photo.
        etudiant.photo = (Etudiant.objects.filter(pk=etudiant.pk).values_list('photo', flat=True).first()
                          if etudiant.pk else None)
        if commit:
            etudiant.save()
            self.save_m2m()
            if self._photo_compressee:
                enregistrer_photo(etudiant, self._photo_compressee, etudiant.matricule or f"etudiant-{etudiant.pk}")
            elif self.cleaned_data.get('retirer_photo'):
                retirer_photo(etudiant)
        return etudiant

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()

    def clean_matricule(self):
        return (self.cleaned_data.get('matricule') or '').strip() or None


class InscriptionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Inscription
        fields = ['session', 'cycle', 'licence', 'semestre', 'filiere', 'promotion', 'redouble']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['filiere'].queryset = Service.objects.filter(programme=True).order_by('nom')
        self.fields['semestre'].queryset = Semestre.objects.select_related('licence').order_by('licence__nom', 'nom')
        self.fields['session'].queryset = Session.objects.order_by('-annee')
        self.fields['cycle'].choices = [('', '— Aucun —')] + Inscription.CYCLE_CHOICES
        # default=0 sur le modèle : la case serait cochée par défaut
        self.initial['redouble'] = bool(self.initial.get('redouble'))          # valeur préremplie, sinon décochée
        if not self.instance.pk:
            session = Session.objects.filter(en_cours=True).first()
            if session:
                self.initial.setdefault('session', session.pk)

    def clean(self):
        donnees = super().clean()
        licence, semestre = donnees.get('licence'), donnees.get('semestre')
        if licence and semestre and semestre.licence_id != licence.id:
            self.add_error('semestre', f"Ce semestre n'appartient pas à la licence {licence}.")
        return donnees

    def valider_unicite(self, etudiant):
        """unique_together (etudiant, session, licence) : l'étudiant n'est pas encore lié au formulaire."""
        if not etudiant.pk:            # nouvel étudiant : aucune inscription possible
            return True
        session, licence = self.cleaned_data.get('session'), self.cleaned_data.get('licence')
        doublon = Inscription.objects.filter(etudiant=etudiant, session=session, licence=licence)
        if self.instance.pk:
            doublon = doublon.exclude(pk=self.instance.pk)
        if doublon.exists():
            self.add_error('licence', "Cet étudiant est déjà inscrit dans cette licence pour cette session.")
            return False
        return True


class ImportForm(BootstrapMixin, forms.Form):
    """
    Une ligne du fichier = une inscription : session, licence, programme et promotion
    viennent du fichier (voir pfe/importer.py).
    """
    fichier = forms.FileField(label="Fichier (.xlsx ou .csv)",
                              widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.csv'}))
    cycle = forms.ChoiceField(label="Cycle", required=False,
                              choices=[('', '— Selon le fichier —')] + Inscription.CYCLE_CHOICES)
    preinscription = forms.BooleanField(label="Préinscription", required=False, initial=False)
    simulation = forms.BooleanField(label="Simulation : vérifier le fichier sans rien enregistrer",
                                    required=False, initial=True)

    def clean_fichier(self):
        f = self.cleaned_data['fichier']
        if not f.name.lower().endswith(('.xlsx', '.csv')):
            raise forms.ValidationError("Seuls les fichiers .xlsx et .csv sont acceptés.")
        return f


# ------------------------------------------------------------------ mon compte

class CoordonneesForm(BootstrapMixin, forms.Form):
    """
    Seuls l'e-mail et le téléphone sont modifiables par l'utilisateur.
    Le formulaire ne connaît aucun autre champ : nom, fonction, services, etc. restent intouchables.
    """
    email = forms.EmailField(label="E-mail", max_length=150)
    telephone = forms.CharField(label="Téléphone", max_length=25, required=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        self.personnel = Personnel.objects.filter(utilisateur=user).first()
        super().__init__(*args, **kwargs)
        if self.personnel is None:
            # Compte sans fiche personnel (ex. superutilisateur) : pas de téléphone enregistrable
            del self.fields['telephone']
        else:
            self.fields['telephone'].required = True
        if not self.is_bound:
            self.initial = {
                'email': self.personnel.email if self.personnel else user.email,
                'telephone': self.personnel.telephone if self.personnel else '',
            }

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        autres_personnels = Personnel.objects.filter(email__iexact=email)
        autres_comptes = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk)
        if self.personnel:
            autres_personnels = autres_personnels.exclude(pk=self.personnel.pk)
        if autres_personnels.exists() or autres_comptes.exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée par un autre compte.")
        return email

    def clean_telephone(self):
        tel = ' '.join(self.cleaned_data['telephone'].split())
        if Personnel.objects.filter(telephone=tel).exclude(pk=self.personnel.pk).exists():
            raise forms.ValidationError("Ce numéro est déjà utilisé par un autre compte.")
        return tel

    def save(self):
        email = self.cleaned_data['email']
        self.user.email = email
        self.user.save(update_fields=['email'])
        if self.personnel:
            self.personnel.email = email
            self.personnel.telephone = self.cleaned_data['telephone']
            self.personnel.save(update_fields=['email', 'telephone'])


class MotDePasseForm(BootstrapMixin, PasswordChangeForm):
    pass


# ------------------------------------------------------------------ gestion du personnel

class PersonnelForm(BootstrapMixin, forms.ModelForm):
    """
    Fiche du personnel et son compte. Le rôle découle des services cochés (voir pfe/roles.py) :
    Service Scolarité → scolarité ; un programme → chef de programme ; autre service → département.
    Seul un super utilisateur peut rattacher quelqu'un au Service Scolarité (droits complets).
    À la création, le compte est créé sans mot de passe : l'invitation par e-mail permet de le choisir.
    """
    identifiant = forms.CharField(label='Identifiant de connexion', max_length=150, required=False,
                                  help_text="Vide : proposé d'après le prénom et le nom (ex. mariam.camara).")
    envoyer_invitation = forms.BooleanField(label="Envoyer l'e-mail d'invitation", required=False, initial=True)

    class Meta:
        model = Personnel
        fields = ['civilite', 'prenom', 'nom', 'fonc', 'email', 'telephone', 'service', 'chef']
        labels = {'fonc': 'Fonction', 'service': 'Services'}
        help_texts = {'chef': "Chef du Service Scolarité, d'un département ou d'un programme. Le chef signe les "
                              "documents (chef de la scolarité ; chef du programme pour le Master) ; seul un chef "
                              "de la scolarité ou d'un département modifie les notes d'une année écoulée.",
                      'fonc': "Imprimée au-dessus de la signature (ex. Le Chef Service de la Scolarité)."}
        widgets = {'service': forms.CheckboxSelectMultiple, 'email': forms.EmailInput()}

    def __init__(self, *args, editeur, **kwargs):
        super().__init__(*args, **kwargs)
        from .roles import est_service_scolarite
        self.editeur = editeur
        f = self.fields
        services = list(Service.objects.order_by('-programme', 'nom'))
        f['service'].queryset = Service.objects.order_by('-programme', 'nom')
        f['service'].required = False                 # message explicite dans clean_service
        f['service'].widget.attrs = {}
        self.services_scolarite = {s.pk for s in services if est_service_scolarite(s)}
        self.groupes_services = [('Programmes (chef de programme)', [s for s in services if s.programme]),
                                 ('Services', [s for s in services if not s.programme])]
        f['prenom'].widget.attrs['placeholder'] = 'Ex. Mariam'
        f['nom'].widget.attrs['placeholder'] = 'Ex. CAMARA'
        f['fonc'].widget.attrs['placeholder'] = 'Ex. Chef de programme'
        f['email'].widget.attrs['placeholder'] = 'nom@exemple.com'
        f['telephone'].widget.attrs['placeholder'] = 'Ex. +224 620 00 00 00'
        if self.instance.pk:
            del f['identifiant'], f['envoyer_invitation']
        else:
            f['identifiant'].widget.attrs['placeholder'] = 'Ex. mariam.camara'

    def services_coches(self):
        """Pour le gabarit : identifiants des services cochés (formulaire envoyé ou fiche existante)."""
        if self.is_bound:
            return {int(v) for v in self.data.getlist(self.add_prefix('service')) if str(v).isdigit()}
        return {s.pk for s in self.instance.service.all()} if self.instance.pk else set()

    def clean_prenom(self):
        return ' '.join(self.cleaned_data['prenom'].split()).title()

    def clean_nom(self):
        return ' '.join(self.cleaned_data['nom'].split()).upper()

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        autres = Personnel.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        comptes = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            comptes = comptes.exclude(pk=self.instance.utilisateur_id)
        if autres.exists() or comptes.exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée par un autre compte.")
        return email

    def clean_telephone(self):
        tel = ' '.join(self.cleaned_data['telephone'].split())
        if Personnel.objects.filter(telephone=tel).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ce numéro est déjà utilisé par un autre membre du personnel.")
        return tel

    def clean_identifiant(self):
        identifiant = (self.cleaned_data.get('identifiant') or '').strip().lower()
        if identifiant:
            if not re.fullmatch(r'[a-z0-9._-]{3,150}', identifiant):
                raise forms.ValidationError("Lettres sans accents, chiffres, points, tirets : 3 caractères au moins.")
            if User.objects.filter(username__iexact=identifiant).exists():
                raise forms.ValidationError(f"L'identifiant « {identifiant} » est déjà pris.")
        return identifiant

    def clean_service(self):
        services = self.cleaned_data['service']
        if not services:
            raise forms.ValidationError("Cochez au moins un service : c'est lui qui donne les droits.")
        avant = {s.pk for s in self.instance.service.all()} if self.instance.pk else set()
        ajoutes = {s.pk for s in services} - avant
        if ajoutes & self.services_scolarite and not self.editeur.is_superuser:
            raise forms.ValidationError("Seul un super utilisateur peut rattacher quelqu'un au Service Scolarité "
                                        "(droits complets sur gePed).")
        return services

    def clean_chef(self):
        chef = self.cleaned_data.get('chef')
        if chef != bool(self.instance.chef) and not self.editeur.is_superuser:
            raise forms.ValidationError("Seul un super utilisateur peut désigner un chef.")
        return chef

    def clean(self):
        d = super().clean()
        services = d.get('service')
        if d.get('chef') and services is not None:
            # Chef : du Service Scolarité, ou d'un programme (département ou non : le chef de programme signe
            # les documents du Master ; les droits de chef sur les notes restent ceux des départements)
            dirige = [s for s in services if s.pk in self.services_scolarite or s.programme]
            if not dirige:
                self.add_error('chef', "Impossible : un chef doit être rattaché au Service Scolarité ou à un "
                                       "programme.")
        return d

    def save(self, commit=True):
        from .comptes import identifiant_propose
        personnel = super().save(commit=False)
        d = self.cleaned_data
        if personnel.utilisateur_id:
            u = personnel.utilisateur
            u.first_name, u.last_name, u.email = d['prenom'], d['nom'], d['email']
            u.save(update_fields=['first_name', 'last_name', 'email'])
        else:
            u = User(username=d.get('identifiant') or identifiant_propose(d['prenom'], d['nom']),
                     first_name=d['prenom'], last_name=d['nom'], email=d['email'], is_active=True)
            u.set_unusable_password()               # le mot de passe sera choisi depuis l'e-mail d'invitation
            u.save()
            personnel.utilisateur = u
        personnel.save()
        self.save_m2m()
        return personnel


# ------------------------------------------------------------------ mot de passe oublié


class MotDePasseOublieForm(BootstrapMixin, PasswordResetForm):
    """
    Retrouve le compte par l'e-mail du compte OU par celui de la fiche personnel
    (les deux peuvent différer pour les comptes créés avant « Mon compte »).
    Le lien est envoyé à l'adresse enregistrée qui correspond à la saisie.
    """
    email = forms.EmailField(label="Adresse e-mail", max_length=254,
                             widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}))

    def get_users(self, email):
        # Comptes actifs, y compris ceux qui n'ont pas encore choisi leur mot de passe (invitation
        # expirée) : ils peuvent le définir avec ce lien. Un compte désactivé ne reçoit rien.
        return User.objects.filter(
            Q(email__iexact=email) | Q(profil_personnel__email__iexact=email), is_active=True
        ).distinct()

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        # user.email peut être vide : on écrit à l'adresse enregistrée qui correspond à la saisie
        saisie = self.cleaned_data['email'].lower()
        user = context['user']
        personnel = getattr(user, 'profil_personnel', None)
        if user.email and user.email.lower() == saisie:
            to_email = user.email
        elif personnel and personnel.email.lower() == saisie:
            to_email = personnel.email
        else:
            return
        super().send_mail(subject_template_name, email_template_name, context, from_email,
                          to_email, html_email_template_name)


class NouveauMotDePasseForm(BootstrapMixin, SetPasswordForm):
    pass


# ------------------------------------------------------------------ attestations

class AttestationInscriptionForm(BootstrapMixin, forms.ModelForm):
    """
    Attestation d'inscription (modèle Attestation) : on saisit le matricule et la licence ;
    la session et le programme viennent de l'inscription de l'étudiant à cette licence.
    Le n° de référence, la date, le type et la session en cours sont fixés par gePed.
    Sert aussi à l'attestation de niveau (même saisie) : voir AttestationNiveauForm.
    """
    TYPE = Attestation.INSCRIPTION
    LIBELLE = "attestation d'inscription"
    matricule = forms.CharField(label="Matricule de l'étudiant", max_length=25)

    class Meta:
        model = Attestation
        fields = ['licence']
        labels = {'licence': 'Licence (niveau)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['licence'].required = True
        self.fields['licence'].queryset = Licence.objects.order_by('id')
        self.fields['matricule'].widget.attrs.update({'placeholder': 'Ex. 2407045829', 'autocomplete': 'off'})
        if self.instance.pk and self.instance.etudiant:
            self.initial['matricule'] = self.instance.etudiant.matricule
        self.inscription = None

    def clean(self):
        donnees = super().clean()
        matricule = (donnees.get('matricule') or '').strip()
        licence = donnees.get('licence')
        if not matricule or not licence:
            return donnees
        etudiant = Etudiant.objects.filter(matricule__iexact=matricule).first()
        if etudiant is None:
            self.add_error('matricule', "Aucun étudiant trouvé avec ce matricule.")
            return donnees
        self.inscription = (Inscription.objects.filter(etudiant=etudiant, licence=licence)
                            .select_related('session', 'filiere').order_by('-session__annee').first())
        if self.inscription is None:
            self.add_error('licence', f"{etudiant.prenom} {etudiant.nom} n'a aucune inscription en {licence}.")
            return donnees
        doublon = Attestation.objects.filter(type=self.TYPE, etudiant=etudiant, licence=licence,
                                             session=self.inscription.session)
        if self.instance.pk:
            doublon = doublon.exclude(pk=self.instance.pk)
        if doublon.exists():
            self.add_error(None, f"Une {self.LIBELLE} existe déjà pour {etudiant.prenom} {etudiant.nom} "
                                 f"en {licence} ({self.inscription.session}) : n° {doublon.first().num_ref}.")
        self.etudiant = etudiant
        return donnees

    def save(self, session_actuelle=None, commit=True):
        attestation = super().save(commit=False)
        attestation.type = self.TYPE
        attestation.etudiant = self.etudiant
        attestation.session = self.inscription.session
        attestation.filiere = self.inscription.filiere
        if not attestation.pk:
            attestation.session_actuelle = session_actuelle
        if commit:
            attestation.save()
        return attestation


class AttestationNiveauForm(AttestationInscriptionForm):
    """Attestation de niveau : matricule + licence, comme l'inscription ; une par étudiant, licence et session."""
    TYPE = Attestation.NIVEAU
    LIBELLE = "attestation de niveau"


class AttestationOrientationForm(BootstrapMixin, forms.ModelForm):
    """
    Attestation d'orientation (modèle Attestation) : on saisit seulement le matricule.
    Le département (programme), la licence et la session viennent de la DERNIÈRE inscription
    de l'étudiant ; INE, PV Bac, profil, centre et session d'orientation viennent de sa fiche.
    Une seule attestation d'orientation par étudiant.
    """
    matricule = forms.CharField(label="Matricule de l'étudiant", max_length=25)

    class Meta:
        model = Attestation
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['matricule'].widget.attrs.update({'placeholder': 'Ex. 2407045829', 'autocomplete': 'off'})
        if self.instance.pk and self.instance.etudiant:
            self.initial['matricule'] = self.instance.etudiant.matricule
        self.inscription = None

    def clean(self):
        donnees = super().clean()
        matricule = (donnees.get('matricule') or '').strip()
        if not matricule:
            return donnees
        etudiant = Etudiant.objects.filter(matricule__iexact=matricule).first()
        if etudiant is None:
            self.add_error('matricule', "Aucun étudiant trouvé avec ce matricule.")
            return donnees
        self.inscription = (Inscription.objects.filter(etudiant=etudiant)
                            .select_related('session', 'licence', 'filiere').order_by('-session__annee', '-id').first())
        if self.inscription is None:
            self.add_error('matricule', f"{etudiant.prenom} {etudiant.nom} n'a aucune inscription : département inconnu.")
            return donnees
        doublon = Attestation.objects.filter(type=Attestation.ORIENTATION, etudiant=etudiant)
        if self.instance.pk:
            doublon = doublon.exclude(pk=self.instance.pk)
        if doublon.exists():
            self.add_error(None, f"{etudiant.prenom} {etudiant.nom} a déjà une attestation d'orientation : "
                                 f"n° {doublon.first().num_ref}.")
        manquants = [nom for nom, v in (('INE', etudiant.ine), ('PV Bac', etudiant.pvbac),
                                        ('profil', etudiant.option_de_bac), ('centre', etudiant.centre_de_bac),
                                        ("session d'orientation", etudiant.annee_orientation)) if not v]
        self.manquants = manquants
        self.etudiant = etudiant
        return donnees

    def save(self, session_actuelle=None, commit=True):
        attestation = super().save(commit=False)
        attestation.type = Attestation.ORIENTATION
        attestation.etudiant = self.etudiant
        attestation.licence = self.inscription.licence
        attestation.session = self.inscription.session
        attestation.filiere = self.inscription.filiere
        if not attestation.pk:
            attestation.session_actuelle = session_actuelle
        if commit:
            attestation.save()
        return attestation


class AttestationFinEtudesForm(BootstrapMixin, forms.ModelForm):
    """
    Attestation de fin d'études (modèle Attestation) : matricule + cycle.
    Le cycle est proposé d'après la dernière inscription de l'étudiant ; le département et la
    session viennent de sa dernière inscription dans ce cycle. Une attestation par étudiant et par cycle.
    """
    matricule = forms.CharField(label="Matricule de l'étudiant", max_length=25)

    class Meta:
        model = Attestation
        fields = ['cycle']
        labels = {'cycle': 'Cycle terminé'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cycle'].required = False
        self.fields['cycle'].choices = [('', "— D'après la dernière inscription —")] + Inscription.CYCLE_CHOICES
        self.fields['matricule'].widget.attrs.update({'placeholder': 'Ex. 2306004423', 'autocomplete': 'off'})
        if self.instance.pk and self.instance.etudiant:
            self.initial['matricule'] = self.instance.etudiant.matricule
        self.inscription = None

    def clean(self):
        donnees = super().clean()
        matricule = (donnees.get('matricule') or '').strip()
        if not matricule:
            return donnees
        etudiant = Etudiant.objects.filter(matricule__iexact=matricule).first()
        if etudiant is None:
            self.add_error('matricule', "Aucun étudiant trouvé avec ce matricule.")
            return donnees
        inscriptions = Inscription.objects.filter(etudiant=etudiant).select_related('session', 'licence', 'filiere')
        cycle = donnees.get('cycle')
        if cycle:
            inscriptions = inscriptions.filter(cycle=cycle)
        self.inscription = inscriptions.order_by('-session__annee', '-id').first()
        if self.inscription is None:
            libelle = dict(Inscription.CYCLE_CHOICES).get(cycle, '')
            self.add_error('cycle' if cycle else 'matricule',
                           f"{etudiant.prenom} {etudiant.nom} n'a aucune inscription"
                           f"{' en ' + libelle if libelle else ''}.")
            return donnees
        self.cycle = cycle or self.inscription.cycle
        if not self.cycle:
            self.add_error('cycle', "Le cycle n'est pas renseigné sur l'inscription de l'étudiant : choisissez-le.")
            return donnees
        doublon = Attestation.objects.filter(type=Attestation.FIN_ETUDES, etudiant=etudiant, cycle=self.cycle)
        if self.instance.pk:
            doublon = doublon.exclude(pk=self.instance.pk)
        if doublon.exists():
            self.add_error(None, f"{etudiant.prenom} {etudiant.nom} a déjà une attestation de fin d'études pour ce "
                                 f"cycle : n° {doublon.first().num_ref}.")
        self.etudiant = etudiant
        self.manquants = [] if etudiant.ine else ['INE']
        return donnees

    def save(self, session_actuelle=None, commit=True):
        attestation = super().save(commit=False)
        attestation.type = Attestation.FIN_ETUDES
        attestation.etudiant = self.etudiant
        attestation.cycle = self.cycle
        attestation.licence = self.inscription.licence
        attestation.session = self.inscription.session
        attestation.filiere = self.inscription.filiere
        if not attestation.pk:
            attestation.session_actuelle = session_actuelle
        if commit:
            attestation.save()
        return attestation


class AttestationSoutenanceForm(BootstrapMixin, forms.ModelForm):
    """
    Attestation de soutenance : matricule (+ cycle si l'étudiant a soutenu dans plusieurs cycles).
    La soutenance doit être notée et validée (≥ 5/10). Programme, session et cycle viennent de la
    soutenance ; thème, jury et signataires de son projet. Une attestation par étudiant et par cycle.
    """
    matricule = forms.CharField(label="Matricule de l'étudiant", max_length=25)

    class Meta:
        model = Attestation
        fields = ['cycle']
        labels = {'cycle': 'Cycle de la soutenance'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cycle'].required = False
        self.fields['cycle'].choices = [('', '— Dernière soutenance —')] + Inscription.CYCLE_CHOICES
        self.fields['matricule'].widget.attrs.update({'placeholder': 'Ex. 2306004423', 'autocomplete': 'off'})
        if self.instance.pk and self.instance.etudiant:
            self.initial['matricule'] = self.instance.etudiant.matricule
        self.soutenance = None

    def clean(self):
        from .notes import regles
        donnees = super().clean()
        matricule = (donnees.get('matricule') or '').strip()
        if not matricule:
            return donnees
        etudiant = Etudiant.objects.filter(matricule__iexact=matricule).first()
        if etudiant is None:
            self.add_error('matricule', "Aucun étudiant trouvé avec ce matricule.")
            return donnees
        soutenances = Soutenance.objects.filter(etudiant=etudiant).select_related('session', 'filiere', 'projet')
        cycle = donnees.get('cycle')
        if cycle:
            soutenances = soutenances.filter(cycle=cycle)
        s = soutenances.order_by('-session__annee', '-id').first()
        if s is None:
            self.add_error('cycle' if cycle else 'matricule',
                           f"{etudiant.prenom} {etudiant.nom} n'a aucune soutenance enregistrée"
                           f"{' en ' + cycle if cycle else ''} (menu Soutenances).")
            return donnees
        seuil = regles()['seuil_validation']
        if s.note_finale is None:
            self.add_error('matricule', f"La soutenance de {etudiant.prenom} {etudiant.nom} n'est pas encore notée.")
            return donnees
        if s.note_finale < seuil:
            self.add_error('matricule', f"La soutenance de {etudiant.prenom} {etudiant.nom} n'est pas validée "
                                        f"(note {s.note_finale:g}/10, il faut au moins {seuil}).")
            return donnees
        doublon = Attestation.objects.filter(type=Attestation.SOUTENANCE, etudiant=etudiant, cycle=s.cycle)
        if self.instance.pk:
            doublon = doublon.exclude(pk=self.instance.pk)
        if doublon.exists():
            self.add_error(None, f"{etudiant.prenom} {etudiant.nom} a déjà une attestation de soutenance pour ce "
                                 f"cycle : n° {doublon.first().num_ref}.")
        p = s.projet
        self.manquants = [nom for nom, v in (
            ('thème', p and p.theme), ('date de soutenance', p and p.date), ('président du jury', p and p.president_jury),
            ('encadreur', p and p.encadreur), ('chef de département', p and p.chef_departement)) if not v]
        self.etudiant, self.soutenance = etudiant, s
        return donnees

    def save(self, session_actuelle=None, commit=True):
        attestation = super().save(commit=False)
        s = self.soutenance
        attestation.type = Attestation.SOUTENANCE
        attestation.etudiant = self.etudiant
        attestation.cycle = s.cycle
        attestation.session = s.session
        attestation.filiere = s.filiere
        inscription = (Inscription.objects.filter(etudiant=self.etudiant, session=s.session)
                       .select_related('licence').first())
        attestation.licence = inscription.licence if inscription else None
        if not attestation.pk:
            attestation.session_actuelle = session_actuelle
        if commit:
            attestation.save()
        return attestation


# ------------------------------------------------------------------ relevés de notes

class ReleveForm(BootstrapMixin, forms.Form):
    """
    Nouveau relevé : matricule + type.
      - Semestre : licence + semestre (session facultative : sinon la dernière inscription à cette licence) ;
      - Licence (relevé annuel) : licence (+ session facultative) ;
      - Extrait : programme (facultatif : sinon celui de la dernière inscription).
    Un relevé déjà délivré pour le même objet n'est pas recréé : il est repris (même numéro).
    """
    TYPES = [('S', 'Relevé semestriel'), ('A', 'Relevé de licence (annuel)'), ('E', 'Extrait du livret universitaire')]
    matricule = forms.CharField(label="Matricule de l'étudiant", max_length=25)
    type = forms.ChoiceField(label='Type de relevé', choices=TYPES, initial='S')
    licence = forms.ModelChoiceField(label='Licence (niveau)', queryset=Licence.objects.none(), required=False)
    semestre = forms.ModelChoiceField(label='Semestre', queryset=Semestre.objects.none(), required=False)
    session = forms.ModelChoiceField(label='Session', queryset=Session.objects.none(), required=False,
                                     help_text="Vide : la dernière inscription de l'étudiant à cette licence.")
    programme = forms.ModelChoiceField(label='Programme', queryset=Service.objects.none(), required=False,
                                       help_text="Vide : le programme de sa dernière inscription.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        f = self.fields
        f['licence'].queryset = Licence.objects.order_by('id')
        f['semestre'].queryset = Semestre.objects.select_related('licence').order_by('licence_id', 'id')
        f['semestre'].label_from_instance = lambda s: f"{s.nom} ({s.licence.nom})"
        f['session'].queryset = Session.objects.order_by('-annee')
        f['session'].empty_label = '— Dernière inscription —'
        f['programme'].queryset = Service.objects.filter(programme=True).order_by('nom')
        f['programme'].empty_label = '— Dernière inscription —'
        f['matricule'].widget.attrs.update({'placeholder': 'Ex. 2306005624', 'autocomplete': 'off'})
        self.cible = None

    def clean(self):
        d = super().clean()
        matricule = (d.get('matricule') or '').strip()
        type_ = d.get('type')
        if not matricule or not type_:
            return d
        etudiant = Etudiant.objects.filter(matricule__iexact=matricule).first()
        if etudiant is None:
            self.add_error('matricule', "Aucun étudiant trouvé avec ce matricule.")
            return d
        inscriptions = (Inscription.objects.filter(etudiant=etudiant, filiere__isnull=False)
                        .select_related('session', 'licence', 'filiere').order_by('-session__annee', '-id'))
        if type_ == 'E':
            programme = d.get('programme') or next((i.filiere for i in inscriptions), None)
            if programme is None or not inscriptions.filter(filiere=programme).exists():
                self.add_error('programme' if d.get('programme') else 'matricule',
                               f"{etudiant.prenom} {etudiant.nom} n'a aucune inscription"
                               f"{' en ' + str(programme) if programme else ' dans un programme'}.")
                return d
            self.cible = {'etudiant': etudiant, 'filiere': programme}
            self._verifier_notes(etudiant, f"en {programme}")
            return d
        licence, semestre, session = d.get('licence'), d.get('semestre'), d.get('session')
        if type_ == 'S' and semestre and not licence:
            licence = semestre.licence
        if licence is None:
            self.add_error('licence', "Choisissez la licence.")
            return d
        insc = inscriptions.filter(licence=licence)
        if session:
            insc = insc.filter(session=session)
        insc = insc.first()
        if insc is None:
            self.add_error('licence', f"{etudiant.prenom} {etudiant.nom} n'a aucune inscription en {licence}"
                                      f"{' pour la session ' + str(session) if session else ''}.")
            return d
        self.cible = {'etudiant': etudiant, 'filiere': insc.filiere, 'session': insc.session, 'licence': insc.licence}
        matieres = Matiere.objects.filter(filiere=insc.filiere, session=insc.session, licence=insc.licence)
        if type_ == 'S':
            if semestre is None:
                self.add_error('semestre', "Choisissez le semestre.")
                return d
            if semestre.licence_id != licence.pk:
                self.add_error('semestre', f"{semestre.nom} n'appartient pas à {licence.nom}.")
                return d
            matieres = matieres.filter(semestre=semestre)
            self.cible['semestre'] = semestre
        perimetre = f"{insc.filiere} · {insc.session} · {licence}" + (f" · {semestre.nom}" if type_ == 'S' else '')
        if not matieres.exists():
            self.add_error(None, f"Aucune matière enregistrée pour {perimetre} : relevé impossible.")
            return d
        self._verifier_notes(etudiant, f"pour {perimetre}")
        return d

    def _verifier_notes(self, etudiant, perimetre):
        """Un relevé sans aucune note n'est pas enregistré."""
        from .releves import a_des_notes
        if not a_des_notes(**self.cible):
            self.add_error(None, f"{etudiant.prenom} {etudiant.nom} n'a aucune note {perimetre} : "
                                 f"le relevé n'est pas enregistré.")

    def save(self):
        """Renvoie (relevé, créé ?)."""
        from .models import ReleveNote
        from .releves import releve_existant
        type_ = self.cleaned_data['type']
        releve = releve_existant(type_, **self.cible)
        if releve is not None:
            return releve, False
        return ReleveNote.objects.create(type=type_, **self.cible), True


# ------------------------------------------------------------------ matières

CREDIT_PAR_DEFAUT = 6


class MatiereForm(BootstrapMixin, forms.ModelForm):
    """
    `programmes` : programmes que l'utilisateur peut choisir (tous pour la scolarité, les siens
    pour un chef de programme). Le semestre doit appartenir à la licence choisie.
    """
    class Meta:
        model = Matiere
        fields = ['nom', 'code', 'credit', 'session', 'filiere', 'cycle', 'licence', 'semestre', 'promotion',
                  'description']
        labels = {'credit': 'Crédits', 'filiere': 'Programme', 'promotion': 'Promotion'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3}),
                   'credit': forms.NumberInput(attrs={'min': 1, 'max': 60})}

    def __init__(self, *args, programmes, **kwargs):
        super().__init__(*args, **kwargs)
        f = self.fields
        f['filiere'].queryset = programmes
        f['filiere'].required = True
        f['filiere'].empty_label = 'Choisir…'
        f['session'].queryset = Session.objects.order_by('-annee')
        f['licence'].queryset = Licence.objects.order_by('id')
        f['semestre'].queryset = Semestre.objects.select_related('licence').order_by('licence_id', 'id')
        f['semestre'].label_from_instance = lambda s: f"{s.nom} ({s.licence.nom})"
        f['promotion'].queryset = Promotion.objects.order_by('nom')
        f['promotion'].required = False
        f['credit'].required = False
        f['cycle'].choices = [('', '— Cycle du programme —')] + Inscription.CYCLE_CHOICES
        f['cycle'].required = False
        f['cycle'].help_text = "Vide : le cycle du programme est repris."
        if not self.instance.pk:
            self.initial.setdefault('credit', CREDIT_PAR_DEFAUT)
            session = Session.objects.filter(en_cours=True).first() or Session.objects.order_by('-annee').first()
            if session:
                self.initial.setdefault('session', session.pk)
            if len(programmes) == 1:                       # un seul programme : choisi d'office
                self.initial.setdefault('filiere', programmes[0].pk)

    def clean_credit(self):
        credit = self.cleaned_data.get('credit')
        if credit is None:
            return CREDIT_PAR_DEFAUT
        if not 1 <= credit <= 60:
            raise forms.ValidationError("Le nombre de crédits doit être compris entre 1 et 60.")
        return credit

    def clean(self):
        d = super().clean()
        # Cycle : celui choisi, sinon celui du programme ; il en faut un
        filiere = d.get('filiere')
        if not d.get('cycle') and filiere is not None:
            d['cycle'] = filiere.cycle or None
        if filiere is not None and not d.get('cycle'):
            self.add_error('cycle', f"Précisez le cycle : le programme {filiere} n'en a pas.")
        semestre, licence = d.get('semestre'), d.get('licence')
        if semestre and licence and semestre.licence_id != licence.pk:
            self.add_error('semestre', f"{semestre.nom} n'appartient pas à {licence.nom} "
                                       f"(il est en {semestre.licence.nom}).")
        nom, filiere, session = (d.get('nom') or '').strip(), d.get('filiere'), d.get('session')
        if nom:
            nom = ' '.join(nom.split())
            d['nom'] = nom
        if nom and filiere and session:
            # Même règle que l'import : sans tenir compte des majuscules ni des accents
            from .importer import cle
            autres = Matiere.objects.filter(filiere=filiere, session=session).exclude(pk=self.instance.pk)
            doublon = next((m for m in autres.only('nom') if cle(m.nom) == cle(nom)), None)
            if doublon:
                self.add_error('nom', f"« {doublon.nom} » existe déjà en {filiere} pour la session {session}.")
        return d

    def validate_unique(self):
        pass        # contrôlé dans clean(), sans tenir compte des majuscules, avec un message clair


class ImportMatieresForm(BootstrapMixin, forms.Form):
    fichier = forms.FileField(label="Fichier (.xlsx ou .csv)",
                              widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.csv'}))
    simulation = forms.BooleanField(label="Simulation : vérifier le fichier sans rien enregistrer",
                                    required=False, initial=True)

    def clean_fichier(self):
        f = self.cleaned_data['fichier']
        if not f.name.lower().endswith(('.xlsx', '.csv')):
            raise forms.ValidationError("Seuls les fichiers .xlsx et .csv sont acceptés.")
        return f


class ImportNotesForm(ImportMatieresForm):
    """
    Même fichier (.xlsx / .csv) et même mode simulation que l'import des matières, plus le PDF de
    l'enseignant (fiche de notes signée) : obligatoire pour un département ou un programme.
    """
    pdf_enseignant = forms.FileField(label="PDF de l'enseignant (fiche de notes signée)", required=False,
                                     widget=forms.ClearableFileInput(attrs={'accept': '.pdf,application/pdf'}))

    def __init__(self, *args, profil=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profil = profil
        if profil is not None and not profil.est_scolarite and not profil.superuser:
            self.fields['pdf_enseignant'].required = True

    def clean_pdf_enseignant(self):
        from .notes import verifier_pdf
        pdf = self.cleaned_data.get('pdf_enseignant')
        if pdf:
            try:
                verifier_pdf(pdf, "Le PDF de l'enseignant")
            except ValueError as e:
                raise forms.ValidationError(str(e))
        return pdf


# ------------------------------------------------------------------ soutenances

def _session_courante():
    return Session.objects.filter(en_cours=True).first() or Session.objects.order_by('-annee').first()


class ChampNote(forms.CharField):
    """Note sur 10 : « 6,5 », « ABS » (= 0) ou vide."""
    def __init__(self, **kwargs):
        kwargs.setdefault('required', False)
        super().__init__(**kwargs)
        self.widget.attrs.update({'inputmode': 'decimal', 'placeholder': 'sur 10 · ABS = 0', 'autocomplete': 'off'})

    def to_python(self, value):
        from .notes import en_note
        try:
            return en_note(value)
        except ValueError as e:
            raise forms.ValidationError(str(e).capitalize() + '.')

    def prepare_value(self, value):
        return ('%g' % value) if isinstance(value, float) else value


class SoutenanceForm(BootstrapMixin, forms.ModelForm):
    """
    Soutenance d'un étudiant : programme, session et promotion (obligatoires), cycle (vide : celui
    du programme), projet de soutenance (facultatif), note et note de session. Une soutenance par
    étudiant et par cycle. Note verrouillée : la scolarité seule la modifie, avec un motif.
    """
    matricule = forms.CharField(label="Matricule de l'étudiant", max_length=25)
    note = ChampNote(label='Note de soutenance')
    note_session = ChampNote(label='Note de session', help_text="Si la soutenance a été repassée : la meilleure "
                                                                 "des deux notes est retenue (mention « Session »).")
    motif = forms.CharField(label='Motif de la modification', required=False,
                            widget=forms.Textarea(attrs={'rows': 2}))
    justificatif = forms.FileField(label='Justificatif (facultatif)', required=False)

    class Meta:
        model = Soutenance
        fields = ['filiere', 'session', 'promotion', 'cycle', 'projet', 'note', 'note_session']
        labels = {'filiere': 'Programme'}

    def __init__(self, *args, profil, **kwargs):
        from .roles import programmes_du_profil
        from .soutenances import notes_de, projets_visibles
        super().__init__(*args, **kwargs)
        self.profil = profil
        f = self.fields
        f['filiere'].queryset = programmes_du_profil(profil)
        f['session'].queryset = Session.objects.order_by('-annee')
        f['promotion'].queryset = Promotion.objects.order_by('nom')
        f['cycle'].choices = [('', '— Cycle du programme —')] + Inscription.CYCLE_CHOICES
        f['cycle'].required = False
        projets = projets_visibles(profil).order_by('-session__annee', 'intitule')
        s = self.instance
        if s.pk:
            projets = projets.filter(filiere_id=s.filiere_id, session_id=s.session_id, promotion_id=s.promotion_id)
            self.initial['matricule'] = s.etudiant.matricule
            f['matricule'].disabled = True
        f['projet'].queryset = projets
        f['projet'].label_from_instance = lambda p: f"{p.intitule} — {p.filiere} · {p.session} · {p.promotion}"
        f['matricule'].widget.attrs.update({'placeholder': 'Ex. 2306004423', 'autocomplete': 'off'})
        self.avant = notes_de(s) if s.pk else None
        self.note_verrouillee = bool(s.pk and s.verrouillee)
        if self.note_verrouillee and not profil.est_scolarite:
            f['note'].disabled = f['note_session'].disabled = True
        if not s.pk:
            session = _session_courante()
            if session:
                self.initial.setdefault('session', session.pk)
            if len(f['filiere'].queryset) == 1:
                self.initial.setdefault('filiere', f['filiere'].queryset[0].pk)
        self.order_fields(['matricule', 'filiere', 'session', 'promotion', 'cycle', 'projet', 'note', 'note_session',
                           'motif', 'justificatif'])

    def clean(self):
        from .soutenances import avertissement_cycle
        d = super().clean()
        if self.instance.pk:
            etudiant = self.instance.etudiant
        else:
            matricule = (d.get('matricule') or '').strip()
            etudiant = Etudiant.objects.filter(matricule=matricule).first() if matricule else None
            if matricule and etudiant is None:
                self.add_error('matricule', f"Aucun étudiant avec le matricule « {matricule} ».")
        if etudiant is None:
            return d
        if etudiant.preinscrit:
            self.add_error('matricule', f"{etudiant.prenom} {etudiant.nom} est préinscrit : finalisez d'abord "
                                        f"son inscription.")
            return d
        filiere, session, promotion, projet = (d.get(k) for k in ('filiere', 'session', 'promotion', 'projet'))
        cycle = d.get('cycle') or (filiere.cycle if filiere else None)
        if filiere and not cycle:
            self.add_error('cycle', f"Précisez le cycle : le programme {filiere} n'en a pas.")
            return d
        d['cycle'] = cycle
        doublon = (Soutenance.objects.filter(etudiant=etudiant, cycle=cycle).exclude(pk=self.instance.pk)
                   .select_related('session').first())
        if doublon:
            self.add_error('matricule', f"{etudiant.prenom} {etudiant.nom} a déjà une soutenance en {cycle} "
                                        f"(session {doublon.session}).")
        if projet and filiere and session and promotion and (projet.filiere_id, projet.session_id,
                                                             projet.promotion_id) != (filiere.pk, session.pk,
                                                                                      promotion.pk):
            self.add_error('projet', f"Le projet « {projet} » est en {projet.filiere} · {projet.session} · "
                                     f"{projet.promotion} : choisissez le même programme, la même session et "
                                     f"la même promotion.")
        if self.note_verrouillee and self.avant != {c: d.get(c) for c in ('note', 'note_session')} \
                and not (d.get('motif') or '').strip() and not self.profil.superuser:
            self.add_error('motif', "Note verrouillée : indiquez le motif de la modification (et joignez le "
                                    "justificatif).")
        self.etudiant = etudiant
        self.avertissement = avertissement_cycle(cycle)
        return d

    def save(self, utilisateur=None):
        """Les modifications d'une note déjà saisie passent par l'historique (avant / après, motif)."""
        from .soutenances import enregistrer_notes
        s = super().save(commit=False)
        s.etudiant, s.cycle = self.etudiant, self.cleaned_data['cycle']
        nouvelles = {c: self.cleaned_data.get(c) for c in ('note', 'note_session')}
        if self.avant is not None:
            for c, v in self.avant.items():                   # notes enregistrées à part, avec la trace
                setattr(s, c, v)
        s.save()
        if self.avant is not None:
            from .notes import MOTIF_SUPER_UTILISATEUR
            motif = (self.cleaned_data.get('motif') or '').strip()
            if not motif and self.note_verrouillee and self.profil.superuser:
                motif = MOTIF_SUPER_UTILISATEUR
            enregistrer_notes([(s, nouvelles)], utilisateur, motif=motif,
                              justificatif=self.cleaned_data.get('justificatif'))
            s.refresh_from_db()
        return s


class ProjetSoutenanceForm(BootstrapMixin, forms.ModelForm):
    """
    Projet de soutenance (1 à 5 étudiants ou plus) : tous les champs en texte libre sont facultatifs.
    Les membres s'ajoutent par leur matricule : leur soutenance est créée (sans note) si besoin.
    """
    ajouter = forms.CharField(label='Ajouter des membres', required=False,
                              widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Matricules séparés par des '
                                                                                     'espaces, virgules ou retours '
                                                                                     'à la ligne'}))

    class Meta:
        model = ProjetSoutenance
        fields = ['intitule', 'theme', 'filiere', 'session', 'promotion', 'cycle', 'statut', 'encadreur',
                  'chef_departement', 'maitre_stage', 'entreprise', 'president_jury', 'examinateur', 'rapporteur',
                  'date', 'heure', 'salle']
        labels = {'filiere': 'Programme'}
        widgets = {'theme': forms.Textarea(attrs={'rows': 2}),
                   'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'heure': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')}

    def __init__(self, *args, profil, **kwargs):
        from .roles import programmes_du_profil
        super().__init__(*args, **kwargs)
        f = self.fields
        f['filiere'].queryset = programmes_du_profil(profil)
        f['session'].queryset = Session.objects.order_by('-annee')
        f['promotion'].queryset = Promotion.objects.order_by('nom')
        f['cycle'].choices = [('', '— Cycle du programme —')] + Inscription.CYCLE_CHOICES
        f['cycle'].required = False
        f['intitule'].widget.attrs['placeholder'] = 'Ex. Projet 12'
        for nom in ('encadreur', 'chef_departement', 'president_jury', 'examinateur', 'rapporteur', 'maitre_stage'):
            f[nom].widget.attrs['placeholder'] = 'Ex. Mariam CAMARA'
        f['chef_departement'].help_text = "Signe le PV de soutenance avec l'encadreur."
        if not self.instance.pk:
            session = _session_courante()
            if session:
                self.initial.setdefault('session', session.pk)
            if len(f['filiere'].queryset) == 1:
                self.initial.setdefault('filiere', f['filiere'].queryset[0].pk)
        self.a_ajouter = []

    def clean(self):
        import re
        from .importer import cle
        d = super().clean()
        filiere, session, promotion = d.get('filiere'), d.get('session'), d.get('promotion')
        if not (filiere and session and promotion):
            return d
        cycle = d.get('cycle') or filiere.cycle or ''
        if not cycle:
            self.add_error('cycle', f"Précisez le cycle : le programme {filiere} n'en a pas.")
            return d
        d['cycle'] = cycle
        intitule = ' '.join((d.get('intitule') or '').split())
        if intitule:
            d['intitule'] = intitule
            autres = (ProjetSoutenance.objects.filter(filiere=filiere, session=session, promotion=promotion)
                      .exclude(pk=self.instance.pk).only('intitule'))
            if any(cle(p.intitule) == cle(intitule) for p in autres):
                self.add_error('intitule', f"« {intitule} » existe déjà en {filiere} · {session} · {promotion}.")
        erreurs = []
        for matricule in dict.fromkeys(m for m in re.split(r'[\s,;]+', d.get('ajouter') or '') if m):
            e = Etudiant.objects.filter(matricule=matricule).first()
            if e is None:
                erreurs.append(f"{matricule} : matricule inconnu.")
                continue
            if e.preinscrit:
                erreurs.append(f"{matricule} ({e.prenom} {e.nom}) est préinscrit.")
                continue
            s = (Soutenance.objects.filter(etudiant=e, cycle=cycle)
                 .select_related('projet', 'filiere', 'session', 'promotion').first())
            if s and s.projet_id and s.projet_id != self.instance.pk:
                erreurs.append(f"{matricule} ({e.prenom} {e.nom}) est déjà dans le projet « {s.projet} ».")
                continue
            if s and (s.filiere_id, s.session_id, s.promotion_id) != (filiere.pk, session.pk, promotion.pk) \
                    and s.projet_id != self.instance.pk:
                erreurs.append(f"{matricule} ({e.prenom} {e.nom}) a déjà une soutenance en {cycle} : "
                               f"{s.filiere} · {s.session} · {s.promotion}.")
                continue
            self.a_ajouter.append((e, s))
        if erreurs:
            self.add_error('ajouter', erreurs)
        return d

    def save(self, commit=True):
        p = super().save()
        # Les membres suivent le programme, la session et la promotion du projet
        Soutenance.objects.filter(projet=p).update(filiere=p.filiere, session=p.session, promotion=p.promotion)
        for e, s in self.a_ajouter:
            if s is None:
                Soutenance.objects.create(etudiant=e, cycle=p.cycle, filiere=p.filiere, session=p.session,
                                          promotion=p.promotion, projet=p)
            elif s.projet_id != p.pk:
                Soutenance.objects.filter(pk=s.pk).update(projet=p)
        return p


class ImportSoutenancesForm(ImportMatieresForm):
    """Même fichier (.xlsx / .csv) et même mode simulation que l'import des matières."""




# ------------------------------------------------------------------ espace étudiant (sans compte)

class IdentificationInscriptionForm(BootstrapMixin, forms.Form):
    ine = forms.CharField(label='INE', max_length=25, widget=forms.TextInput(attrs={
        'placeholder': 'Ex. CAMM0511984521', 'autocomplete': 'off', 'autocapitalize': 'characters'}))
    pvbac = forms.CharField(label='Numéro de PV du bac', max_length=25, widget=forms.TextInput(attrs={
        'placeholder': 'Ex. 184882', 'autocomplete': 'off', 'inputmode': 'numeric'}))


class IdentificationReinscriptionForm(BootstrapMixin, forms.Form):
    ine = forms.CharField(label='INE', max_length=25, widget=forms.TextInput(attrs={
        'placeholder': 'Ex. CAMM0511984521', 'autocomplete': 'off', 'autocapitalize': 'characters'}))
    matricule = forms.CharField(label='Matricule', max_length=25, widget=forms.TextInput(attrs={
        'placeholder': 'Ex. 2306004423', 'autocomplete': 'off'}))


class IdentificationMasterForm(BootstrapMixin, forms.Form):
    email = forms.EmailField(label='Adresse e-mail', max_length=150, widget=forms.EmailInput(attrs={
        'placeholder': 'Ex. prenom.nom@gmail.com', 'autocomplete': 'email'}))


class DossierEtudiantForm(BootstrapMixin, forms.Form):
    """
    Ce que l'étudiant peut proposer dans son espace : contacts, photo (déjà recadrée et compressée
    par la fenêtre photo), et pour une réinscription le niveau, la bourse et le type de formation.
    Tous les contacts sont OBLIGATOIRES (l'étudiant fournit des informations à jour) ; la ville
    n'est pas demandée. Tout reste en attente jusqu'à la validation par la scolarité.
    """
    CHIFFRES_MIN = 8                      # un numéro de téléphone guinéen compte 9 chiffres (8 au minimum ici)
    telephone = forms.CharField(label='Téléphone', max_length=25, widget=forms.TextInput(attrs={
        'placeholder': 'Ex. 620 00 00 00', 'inputmode': 'tel', 'autocomplete': 'tel'}))
    email = forms.EmailField(label='E-mail', max_length=150, widget=forms.EmailInput(attrs={
        'placeholder': 'nom@exemple.com', 'autocomplete': 'email'}))
    adresse = forms.CharField(label='Adresse', max_length=200,
                              widget=forms.TextInput(attrs={'placeholder': 'Quartier/Mamou'}))
    tuteur = forms.CharField(label='Nom du tuteur', max_length=100,
                             widget=forms.TextInput(attrs={'placeholder': 'Ex. Mariam CAMARA'}))
    contact_de_tuteur = forms.CharField(label='Contact du tuteur', max_length=25, widget=forms.TextInput(attrs={
        'placeholder': 'Ex. 622 00 00 00', 'inputmode': 'tel'}))
    photo = forms.FileField(label='Photo', required=False,
                            widget=forms.FileInput(attrs={'accept': 'image/*', 'class': 'visually-hidden'}))
    licence = forms.ModelChoiceField(label='Niveau demandé', queryset=Licence.objects.none(), required=False,
                                     empty_label=None)
    boursier = forms.ChoiceField(label='Bourse', choices=Etudiant.BOURSIER_CHOICES, required=False,
                                 widget=forms.RadioSelect(attrs={'class': 'btn-check'}))
    type_formation = forms.ChoiceField(label='Type de formation', choices=Etudiant.TYPE_FORMATION_CHOICES,
                                       required=False, widget=forms.RadioSelect(attrs={'class': 'btn-check'}))
    # Signature électronique simple : certification + signature dessinée, obligatoires à chaque envoi
    certifie = forms.BooleanField(
        label="Je certifie l'exactitude des informations fournies et m'engage à respecter le règlement intérieur "
              "de l'Institut Supérieur de Technologie de Mamou.",
        error_messages={'required': "Cochez la case pour certifier vos informations."})
    signature = forms.CharField(widget=forms.HiddenInput(attrs={'data-signature-valeur': ''}), max_length=400_000,
                                error_messages={'required': "Signez dans le cadre avant d'envoyer votre demande."})

    def __init__(self, *args, reinscription=False, niveaux=(), etudiant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reinscription, self.etudiant = reinscription, etudiant
        if reinscription:
            self.fields['licence'].queryset = Licence.objects.filter(pk__in=[n.pk for n in niveaux])
            self.fields['licence'].required = True
            self.fields['boursier'].required = self.fields['type_formation'].required = True
        else:
            for champ in ('licence', 'boursier', 'type_formation'):
                del self.fields[champ]

    @classmethod
    def _numero(cls, valeur):
        valeur = ' '.join((valeur or '').split())
        if len(re.sub(r'\D', '', valeur)) < cls.CHIFFRES_MIN:
            raise forms.ValidationError(f"Numéro incomplet : {cls.CHIFFRES_MIN} chiffres au moins.")
        if not re.fullmatch(r'[+\d][\d .\-]*', valeur):
            raise forms.ValidationError("Numéro invalide : chiffres, espaces et « + » seulement.")
        return valeur

    def clean_telephone(self):
        return self._numero(self.cleaned_data['telephone'])

    def clean_contact_de_tuteur(self):
        return self._numero(self.cleaned_data['contact_de_tuteur'])

    def clean_adresse(self):
        return ' '.join(self.cleaned_data['adresse'].split())

    def clean_tuteur(self):
        return ' '.join(self.cleaned_data['tuteur'].split())

    def clean(self):
        d = super().clean()
        tel, tuteur = d.get('telephone'), d.get('contact_de_tuteur')
        if tel and tuteur and re.sub(r'\D', '', tel)[-9:] == re.sub(r'\D', '', tuteur)[-9:]:
            self.add_error('contact_de_tuteur', "Le contact du tuteur doit être différent de votre propre numéro.")
        return d

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if email and Etudiant.objects.filter(email__iexact=email).exclude(pk=getattr(self.etudiant, 'pk', None)).exists():
            # Message neutre : ne pas révéler qu'une adresse appartient à un autre étudiant
            raise forms.ValidationError("Cette adresse e-mail ne peut pas être utilisée : indiquez-en une autre.")
        return email

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo:
            try:
                return compresser(photo)               # octets JPEG (≤ 350 Ko, 600 × 800)
            except ValidationError as e:
                raise forms.ValidationError(e.messages)
        return None

    def clean_signature(self):
        """Signature dessinée (image PNG en data URL) → PNG noir et blanc, rognée, légère ; refusée si vide."""
        try:
            return preparer_signature(self.cleaned_data['signature'])
        except ValueError as e:
            raise forms.ValidationError(str(e))


def preparer_signature(data_url):
    """
    « data:image/png;base64,… » envoyée par le cadre de signature → octets PNG en niveaux de gris,
    rognés autour du tracé (fond blanc). Lève ValueError si l'image est illisible, trop grande ou vide.
    """
    import base64
    import binascii
    import io as _io
    from PIL import Image, ImageOps, UnidentifiedImageError
    entete, _, donnees = (data_url or '').partition(',')
    if entete != 'data:image/png;base64' or not donnees:
        raise ValueError("Signature illisible : signez de nouveau dans le cadre.")
    try:
        brut = base64.b64decode(donnees, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Signature illisible : signez de nouveau dans le cadre.")
    if len(brut) > 300 * 1024:
        raise ValueError("Signature trop lourde : signez de nouveau dans le cadre.")
    try:
        img = Image.open(_io.BytesIO(brut))
        if img.width * img.height > 4_000_000:
            raise ValueError("Signature trop grande : signez de nouveau dans le cadre.")
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Signature illisible : signez de nouveau dans le cadre.")
    # Fond blanc sous la transparence, niveaux de gris, encre = pixels sombres
    fond = Image.new('RGB', img.size, (255, 255, 255))
    fond.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
    gris = fond.convert('L')
    encre = gris.point(lambda v: 255 if v < 160 else 0)
    zone = encre.getbbox()
    nb_points = sum(encre.histogram()[255:])
    if not zone or nb_points < 120 or (zone[2] - zone[0]) < 40:
        raise ValueError("Signature trop courte ou vide : signez lisiblement dans le cadre.")
    marge = 8
    zone = (max(0, zone[0] - marge), max(0, zone[1] - marge), min(gris.width, zone[2] + marge),
            min(gris.height, zone[3] + marge))
    signee = ImageOps.autocontrast(gris.crop(zone))
    signee.thumbnail((900, 300))
    flux = _io.BytesIO()
    signee.save(flux, 'PNG', optimize=True)
    return flux.getvalue()
