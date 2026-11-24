"""
Recherche des fonctionnalités (champ « Rechercher une fonctionnalité » du bandeau, raccourci Ctrl + K).

Catalogue : une ligne par fonctionnalité (rubrique, titre, explication, mots-clés, page, icône, droit).
Le droit reprend celui de la vue (décorateurs de pfe/roles.py) : chacun ne reçoit que les pages
qu'il peut ouvrir. Le filtrage du texte tapé se fait ensuite dans le navigateur (pfe/js/gped-recherche.js).
Une nouvelle page dans GePed = une ligne ici (le test vérifie que chaque rôle peut ouvrir ce qu'il voit).
"""
from django.urls import reverse


# ------------------------------------------------------------------ droits (mêmes règles que les vues)

def _acces(profil, user):            # @acces_requis
    return profil.a_acces


def _connecte(profil, user):         # @login_required seulement
    return True


def _etudiants(profil, user):        # @etudiants_requise : scolarité, directeur de Master
    return profil.gere_etudiants


def _attestations(profil, user):     # @attestations_requise
    return profil.gere_attestations


def _scolarite(profil, user):        # @scolarite_requise
    return profil.est_scolarite


def _matieres(profil, user):         # @gestion_matieres_requise : scolarité, chef de programme
    return profil.gere_matieres


def _soutenances(profil, user):      # @soutenances_lecture_requise : + autres services et départements (lecture)
    return profil.voit_soutenances


def _demandes(profil, user):         # @demandes_lecture_requise : + Direction (lecture)
    return profil.consulte_demandes


def _matieres_lecture(profil, user):  # @matieres_lecture_requise : tout le personnel sauf la Direction
    return profil.voit_matieres


def _autorisations(profil, user):    # @autorisations_lecture_requise : scolarité + Direction (lecture)
    return profil.voit_autorisations


def _voit_enseignants(profil, user):   # @enseignants_lecture_requise
    return profil.voit_enseignants


def _gere_enseignants(profil, user):   # @enseignants_gestion_requise : Direction des Études, super utilisateur
    return profil.gere_enseignants


def _voit_salles(profil, user):        # @salles_lecture_requise
    return profil.voit_salles


def _gere_salles(profil, user):        # @salles_gestion_requise : Direction des Études, super utilisateur
    return profil.gere_salles


def _voit_emplois(profil, user):       # @emplois_lecture_requise
    return profil.voit_emplois


def _enseignant(profil, user):         # @espace_enseignant_requis
    return profil.est_enseignant


def _lit_notes(profil, user):        # @lecture_notes_requise : + Direction Générale des Études
    return profil.lit_notes


def _validations(profil, user):      # @validations_requises : chef de département, super utilisateur
    return profil.voit_validations


def _suivi_notes(profil, user):      # @suivi_notes_requis : scolarité, admin, Direction Générale des Études
    return profil.voit_suivi_notes


def _conges(profil, user):           # @lecture_conges_requise
    return profil.voit_conges


def _superuser(profil, user):        # @superuser_requis
    return user.is_superuser


def _guide(profil, user):            # un guide est déposé pour ce type de compte
    from .guides import guide_courant, public_du_profil
    return guide_courant(public_du_profil(profil)) is not None


def _admin(modele):
    """Espace admin : compte « équipe » ayant le droit de consulter ce modèle."""
    def droit(profil, user):
        return user.is_staff and user.has_perm(f'pfe.view_{modele}')
    return droit


def _staff(profil, user):
    return user.is_staff


ATTESTATION = "attestation certificat document imprimer délivrer"
NOTES = "note notes moyenne examen contrôle TP"
SOUTENANCE = "soutenance mémoire PFE projet de fin d'études jury"
ADMIN = "administration réglage paramètre"

# (rubrique, titre, explication, mots-clés, nom d'URL, arguments, icône, droit)
CATALOGUE = [
    ('Général', 'Tableau de bord', "Vue d'ensemble : effectifs de la session, préinscrits, raccourcis.",
     'accueil résumé chiffres', 'tableau_de_bord', (), 'bi-grid-1x2', _acces),

    ('Étudiants', 'Liste des étudiants',
     "Rechercher un étudiant, ouvrir sa fiche, modifier ou supprimer une inscription, exporter la liste.",
     'étudiant fiche chercher rechercher matricule INE modifier supprimer inscription exporter Excel CSV',
     'etudiants_liste', (), 'bi-people', _acces),
    ('Étudiants', 'Préinscriptions', "Préinscrits de la session (nouveaux bacheliers orientés) : les inscrire.",
     'préinscrit préinscription nouveau bachelier orienté inscrire', 'preinscriptions_liste', (), 'bi-person-plus',
     _acces),
    ('Étudiants', 'Inscrire un étudiant', "Inscription d'un nouvel étudiant, ou réinscription d'un ancien, pour une session.",
     'inscription réinscription inscrire ajouter nouvel étudiant', 'inscription_creer', (), 'bi-person-add',
     _etudiants),
    ('Étudiants', 'Importer une liste d\'étudiants',
     "Importer un fichier Excel d'étudiants et d'inscriptions, avec une simulation avant d'enregistrer.",
     'import importer Excel xlsx fichier liste orientés', 'importer', (), 'bi-upload', _scolarite),
    ('Étudiants', 'Demandes en ligne',
     "Inscriptions et réinscriptions déposées dans l'espace étudiant : valider, refuser, imprimer la fiche.",
     "espace étudiant en ligne demande valider refuser fiche d'inscription imprimer dossier pièces",
     'demandes_liste', (), 'bi-inbox', _demandes),
    ('Étudiants', 'Ouvrir ou fermer les inscriptions en ligne',
     "Ouvrir ou fermer les inscriptions et les réinscriptions de l'espace étudiant pour la session.",
     'ouvrir fermer ouverture espace étudiant en ligne dépôts', 'demandes_liste', (), 'bi-toggle-on', _scolarite),
    ('Étudiants', "Page publique de l'espace étudiant",
     "La page où les étudiants font leur demande d'inscription, de réinscription ou de Master.",
     'espace étudiant lien public en ligne master site', 'espace_accueil', (), 'bi-globe2', _etudiants),
    ('Étudiants', 'Congés académiques',
     "Étudiants en congé : inscription de l'année suspendue, justificatif, levée du congé.",
     'congé congés académique suspendre suspension suspendu interruption pause reprise lever justificatif',
     'conges_liste', (), 'bi-pause-circle', _conges),
    ('Étudiants', 'Statistiques', "Effectifs par niveau, programme, sexe et âge, redoublants, export Excel.",
     'statistiques effectifs chiffres graphique redoublants filles garçons hommes femmes âge', 'statistiques', (),
     'bi-bar-chart', _acces),

    ('Documents', "Attestation d'inscription", "Délivrer ou réimprimer une attestation d'inscription.",
     ATTESTATION + ' inscription', 'attestations_type', ('inscription',), 'bi-file-earmark-text', _attestations),
    ('Documents', 'Attestation de niveau', "Délivrer ou réimprimer une attestation de niveau (cours suivis).",
     ATTESTATION + ' niveau', 'attestations_type', ('niveau',), 'bi-file-earmark-text', _attestations),
    ('Documents', "Attestation de fin d'études", "Délivrer ou réimprimer une attestation de fin d'études (diplômés).",
     ATTESTATION + " fin d'études diplôme diplômé réussite", 'attestations_type', ('fin-etudes',),
     'bi-file-earmark-text', _attestations),
    ('Documents', "Attestation d'orientation", "Délivrer ou réimprimer une attestation d'orientation.",
     ATTESTATION + ' orientation', 'attestations_type', ('orientation',), 'bi-file-earmark-text', _attestations),
    ('Documents', 'Attestation de soutenance', "Délivrer ou réimprimer une attestation de soutenance.",
     ATTESTATION + ' ' + SOUTENANCE, 'attestations_type', ('soutenance',), 'bi-file-earmark-text', _attestations),
    ('Documents', 'Relevés de notes',
     "Délivrer un relevé semestriel, annuel ou un extrait du parcours ; réimprimer un relevé délivré.",
     'relevé relevés bulletin notes extrait semestre annuel imprimer délivrer', 'releves', (),
     'bi-file-earmark-spreadsheet', _etudiants),

    ('Pédagogie', 'Matières', "Matières par programme, niveau et semestre, avec leurs crédits.",
     'matière matières cours module UE unité crédits programme', 'matieres_liste', (), 'bi-journal-bookmark', _matieres_lecture),
    ('Pédagogie', 'Ajouter une matière', "Créer une matière d'un programme, d'un niveau et d'un semestre.",
     'matière cours module créer ajouter nouvelle', 'matiere_creer', (), 'bi-journal-plus', _matieres),
    ('Pédagogie', 'Importer des matières', "Importer les matières d'un fichier Excel.",
     'matières cours import importer Excel fichier', 'matieres_importer', (), 'bi-upload', _matieres),
    ('Notes', 'Liste des notes', "Toutes les notes saisies, avec filtres ; corriger la note d'un étudiant.",
     NOTES + ' liste corriger modifier', 'notes_liste', (), 'bi-123', _lit_notes),
    ('Notes', 'Saisie des notes', "Saisir les notes d'une matière (session, programme, niveau, semestre).",
     NOTES + ' saisie saisir rattrapage grille', 'notes_matieres', (), 'bi-pencil-square', _matieres),
    ('Notes', 'Importer des notes', "Importer les notes d'une matière depuis un fichier Excel.",
     NOTES + ' import importer Excel fichier', 'notes_importer', (), 'bi-upload', _matieres),
    ('Notes', 'Résultats et délibérations',
     "Moyennes, crédits, rangs et mentions par semestre, année et cycle ; procès-verbal de délibération.",
     'résultats délibération délibérations PV procès-verbal moyenne rang classement mention admis ajourné crédits',
     'notes_resultats', (), 'bi-clipboard-data', _lit_notes),
    ('Notes', 'Validation des semestres',
     "Chef de département : valider les notes de chaque semestre avant la délibération ; suivi de la saisie.",
     NOTES + ' validation valider semestre chef de département avancement saisie délibération',
     'notes_validations', (), 'bi-patch-check', _validations),
    ('Notes', 'Espace de délibération',
     "Grille étudiants × matières à présenter au jury : qui a tout validé, pas tout, N matières ; répartition.",
     NOTES + ' délibération jury PV grille validé validées tout validé non validé statistiques répartition projeter',
     'notes_deliberation', (), 'bi-easel', _lit_notes),
    ('Notes', 'Historique des notes',
     "Qui a modifié quelle note, quand, avant / après, motif, justificatif et fichier importé d'origine.",
     NOTES + ' historique modification modifié trace traçabilité auteur qui justificatif audit',
     'notes_historique', (), 'bi-clock-history', _suivi_notes),
    ('Notes', 'Fichiers importés',
     "Fichiers Excel et PDF de l'enseignant de chaque import de notes, avec le programme qui importe.",
     NOTES + ' import importés fichier Excel PDF enseignant télécharger journal',
     'notes_imports', (), 'bi-file-earmark-arrow-up', _suivi_notes),
    ('Notes', 'Autorisations de modification',
     "Rouvrir aux programmes, pour une période, les notes d'un niveau délibéré ou d'une année écoulée.",
     NOTES + ' autorisation autoriser rouvrir ouvrir délibéré période date début fin programme niveau',
     'notes_autorisations', (), 'bi-unlock', _autorisations),
    ('Enseignants', 'Enseignants', "Liste des enseignants par département : grade, statut, spécialité, contact.",
     'enseignant enseignants professeur professeurs vacataire permanent département corps enseignant liste',
     'enseignants_liste', (), 'bi-person-video3', _voit_enseignants),
    ('Enseignants', 'Ajouter un enseignant', "Enregistrer un enseignant et son compte (ou le rattacher au compte "
     "existant d'un membre du personnel).", 'enseignant ajouter nouveau enregistrer compte',
     'enseignant_creer', (), 'bi-person-plus', _gere_enseignants),
    ('Enseignants', 'Espace enseignant', "Votre espace enseignant.", 'espace enseignant mon espace emploi du temps',
     'espace_enseignant', (), 'bi-person-video3', _enseignant),
    ('Emplois du temps', 'Emploi du temps', "Semaine type d'une classe, d'un enseignant ou d'une salle : cours, "
     "salles, enseignants et conflits.", 'emploi du temps planning horaire cours semaine créneau grille séance',
     'emploi_du_temps', (), 'bi-calendar-week', _voit_emplois),
    ('Salles', 'Salles et bâtiments', "Salles de cours, amphithéâtres, laboratoires : capacité, type, bâtiment.",
     'salle salles bâtiment batiment amphi amphithéâtre laboratoire capacité places classe local',
     'salles', (), 'bi-door-open', _voit_salles),
    ('Salles', 'Importer des salles', "Importer la liste des salles depuis un fichier Excel ou CSV.",
     'salle salles importer import Excel CSV fichier bâtiment', 'salles_importer', (), 'bi-upload', _gere_salles),
    ('Soutenances', 'Liste des soutenances', "Soutenances enregistrées : jury, date, note.",
     SOUTENANCE + ' liste', 'soutenances', (), 'bi-mortarboard', _soutenances),
    ('Soutenances', 'Ajouter une soutenance', "Enregistrer une soutenance : étudiant, jury, date et salle.",
     SOUTENANCE + ' ajouter créer nouvelle', 'soutenance_creer', (), 'bi-plus-circle', _matieres),
    ('Soutenances', 'Projets de soutenance', "Sujets et encadreurs des projets de fin d'études.",
     SOUTENANCE + ' sujet encadreur thème', 'projets_liste', (), 'bi-lightbulb', _soutenances),
    ('Soutenances', 'Ajouter un projet de soutenance', "Enregistrer un sujet de projet et son encadreur.",
     SOUTENANCE + ' sujet encadreur thème ajouter créer', 'projet_creer', (), 'bi-plus-circle', _matieres),
    ('Soutenances', 'Notes de soutenance', "Saisir les notes de soutenance.",
     SOUTENANCE + ' note notes saisie saisir', 'soutenances_notes', (), 'bi-pencil-square', _matieres),
    ('Soutenances', 'Calendrier des soutenances', "Planning des soutenances par date et par salle.",
     SOUTENANCE + ' calendrier planning date salle programme', 'soutenances_calendrier', (), 'bi-calendar3',
     _soutenances),
    ('Soutenances', 'Importer des soutenances', "Importer des soutenances depuis un fichier Excel.",
     SOUTENANCE + ' import importer Excel fichier', 'soutenances_importer', (), 'bi-upload', _matieres),

    ('Compte', "Guide d'utilisation", "Le guide PDF de votre compte, pas à pas, avec des captures d'écran.",
     'guide aide manuel tutoriel formation documentation pdf mode emploi', 'guide_ouvrir', (), 'bi-book', _guide),
    ('Administration', "Guides d'utilisation (dépôt)", "Déposer le guide PDF de chaque type de compte et ses nouveautés.",
     'guide guides dépôt déposer manuel pdf nouveautés version', 'guides_gestion', (), 'bi-journal-arrow-up',
     _superuser),
    ('Compte', 'Mon compte', "Votre profil, votre photo et votre mot de passe.",
     'compte profil photo mot de passe changer modifier', 'mon_compte', (), 'bi-person-gear', _connecte),
    ('Administration', 'Personnel', "Comptes du personnel : services, chefs, droits d'accès.",
     'personnel agents utilisateurs comptes chef enseignants accès droits rôle', 'personnel_liste', (),
     'bi-person-badge', _superuser),
    ('Administration', 'Ajouter un membre du personnel', "Créer la fiche et le compte d'un membre du personnel.",
     'personnel agent utilisateur compte créer ajouter nouveau', 'personnel_creer', (), 'bi-person-plus', _superuser),
    ('Administration', 'Sessions (années académiques)', "Années académiques et session en cours.",
     ADMIN + ' session année académique en cours', 'admin:pfe_session_changelist', (), 'bi-calendar-range',
     _admin('session')),
    ('Administration', 'Promotions et niveaux par session',
     "Niveau de chaque promotion pour chaque session : sert aux inscriptions en ligne.",
     ADMIN + ' promotion niveau session prévu inscriptions en ligne', 'admin:pfe_promotion_changelist', (),
     'bi-diagram-3', _admin('promotion')),
    ('Administration', 'Semestres', "Semestres de chaque niveau.",
     ADMIN + ' semestre semestres niveau', 'admin:pfe_semestre_changelist', (), 'bi-layers', _admin('semestre')),
    ('Administration', 'Niveaux', "Niveaux d'études (Licence 1, M1…).",
     ADMIN + ' niveau licence master année', 'admin:pfe_licence_changelist', (), 'bi-bar-chart-steps',
     _admin('licence')),
    ('Administration', 'Services et programmes', "Services, départements et programmes (filières).",
     ADMIN + ' service département programme filière', 'admin:pfe_service_changelist', (), 'bi-diagram-2',
     _admin('service')),
    ('Administration', "Réglages d'affichage", "Nombre de relevés délivrés affichés dans la liste.",
     ADMIN + " affichage nombre relevés liste", 'admin:pfe_reglageaffichage_changelist', (), 'bi-sliders',
     _admin('reglageaffichage')),
    ('Administration', 'Espace administration', "Toutes les tables de GePed (administration Django).",
     ADMIN + ' admin django tables', 'admin:index', (), 'bi-sliders', _staff),
]


def fonctionnalites(profil, user):
    """Fonctionnalités que cet utilisateur peut ouvrir, prêtes pour la recherche (JSON du bandeau)."""
    return [{'r': rubrique, 't': titre, 'd': explication, 'k': mots, 'u': reverse(nom, args=args), 'i': icone}
            for rubrique, titre, explication, mots, nom, args, icone, droit in CATALOGUE if droit(profil, user)]
