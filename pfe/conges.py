"""
Congés académiques (modèle CongeAcademique).

  - L'inscription de l'année du congé est suspendue : hors de la liste des étudiants, des
    statistiques, des grilles de notes et des résultats ; relevés et attestations restent possibles.
  - Congé en cours (pas encore levé) : aucune nouvelle inscription pour l'étudiant (scolarité,
    import, demande en ligne) ; voir bloquer_inscription().
  - Après la levée, la reprise se fait au même niveau (espace_etudiant.niveaux_prevus).
Le justificatif (un seul fichier) suit les règles des pièces du dossier (pieces.preparer) : PDF
compressé rangé dans le stockage privé, jamais publié.
"""
import re

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from . import pieces
from .models import CongeAcademique, stockage_prive

MESSAGE_BLOCAGE = ("{etudiant} est en congé académique depuis le {debut:%d/%m/%Y} ({session}) : aucune inscription "
                   "n'est possible tant que le congé n'est pas levé.")


def conge_en_cours(etudiant_id):
    """Congé pas encore levé de l'étudiant (None s'il n'en a pas)."""
    return (CongeAcademique.objects.filter(inscription__etudiant_id=etudiant_id, levee_le__isnull=True)
            .select_related('inscription__session', 'inscription__etudiant').first())


def etudiants_en_conge(etudiant_ids=None):
    """Identifiants des étudiants dont un congé est en cours (parmi `etudiant_ids` si donné)."""
    qs = CongeAcademique.objects.filter(levee_le__isnull=True)
    if etudiant_ids is not None:
        qs = qs.filter(inscription__etudiant_id__in=list(etudiant_ids))
    return set(qs.values_list('inscription__etudiant_id', flat=True))


def message_blocage(conge):
    e = conge.inscription.etudiant
    return MESSAGE_BLOCAGE.format(etudiant=f"{e.prenom} {e.nom}".strip(), debut=conge.date_debut,
                                  session=conge.inscription.session)


def bloquer_inscription(etudiant_id):
    """Lève ValidationError si un congé en cours interdit une nouvelle inscription."""
    conge = conge_en_cours(etudiant_id)
    if conge is not None:
        raise ValidationError(message_blocage(conge))


def _ranger_justificatif(conge, fichiers):
    octets, nb_pages = pieces.preparer(fichiers)
    e = conge.inscription.etudiant
    reference = re.sub(r'[^A-Za-z0-9]+', '', e.matricule or '') or f"etudiant{e.pk}"
    nom = stockage_prive().save(f"conges/{timezone.now():%Y}/{reference}_conge_{timezone.now():%Y%m%d%H%M%S}.pdf",
                                ContentFile(octets))
    return nom, nb_pages, len(octets)


def _supprimer_fichier(nom):
    if nom:
        stockage = stockage_prive()
        if stockage.exists(nom):
            stockage.delete(nom)


def enregistrer(inscription, motif, date_debut, date_fin, fichiers, utilisateur, conge=None):
    """
    Accorde un congé (conge=None) ou modifie celui de l'inscription. Justificatif obligatoire à la
    création, remplacé s'il est renvoyé. Lève ValidationError (message clair) si c'est impossible.
    """
    from .notes import recalculer
    if not (motif or '').strip():
        raise ValidationError("Indiquez le motif du congé.")
    if date_debut is None:
        raise ValidationError("Indiquez la date de début du congé.")
    if date_fin is not None and date_fin < date_debut:
        raise ValidationError("La fin du congé doit suivre son début.")
    fichiers = [f for f in fichiers if f]
    if conge is None:
        if inscription.etudiant.preinscrit:
            raise ValidationError("Un préinscrit n'a pas encore d'inscription validée : pas de congé possible.")
        autre = conge_en_cours(inscription.etudiant_id)
        if autre is not None:
            raise ValidationError(f"Un congé est déjà en cours ({autre.inscription.session}) : levez-le d'abord.")
        if not fichiers:
            raise ValidationError("Ajoutez le justificatif du congé : un PDF, ou les photos des pages.")
        conge = CongeAcademique(inscription=inscription, accorde_par=utilisateur)
    ancien = conge.justificatif.name if fichiers else None
    if fichiers:
        conge.justificatif, conge.pages, conge.taille = _ranger_justificatif(conge, fichiers)
    conge.motif, conge.date_debut, conge.date_fin = motif.strip(), date_debut, date_fin
    with transaction.atomic():
        conge.save()
    _supprimer_fichier(ancien)
    recalculer([inscription.etudiant_id])            # l'année du congé quitte (ou garde) les résultats
    return conge


def lever(conge, utilisateur):
    """Fin du congé : l'étudiant peut de nouveau s'inscrire (au même niveau) ; l'année reste suspendue."""
    conge.levee_le, conge.levee_par = timezone.now(), utilisateur
    conge.date_fin = conge.date_fin or timezone.localdate()
    conge.save(update_fields=['levee_le', 'levee_par', 'date_fin'])
    return conge


def annuler(conge):
    """Congé saisi par erreur : supprimé avec son justificatif, l'inscription redevient normale."""
    from .notes import recalculer
    etudiant_id, nom = conge.inscription.etudiant_id, conge.justificatif.name
    conge.delete()
    _supprimer_fichier(nom)
    recalculer([etudiant_id])


def ouvrir_justificatif(conge):
    return stockage_prive().open(conge.justificatif.name, 'rb')
