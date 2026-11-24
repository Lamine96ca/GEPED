"""
Exclusions d'un programme (modèle Exclusion).

- GePed PROPOSE les étudiants à exclure d'après leurs dettes académiques non résorbées : notes sous le seuil de
  validation dans des semestres délibérés, sous un règlement qui prévoit des dettes et un seuil d'exclusion
  (settings.GPED_NOTES['reglements'][code]['seuil_exclusion'], ex. 30 crédits pour l'arrêté 877, art. 68 et 69).
  Une dette rattrapée (note remontée au-dessus du seuil) n'est plus comptée.
- La scolarité PRONONCE l'exclusion (dettes, indiscipline, abandon, fraude, autre) depuis la fiche de l'étudiant
  ou depuis la liste des propositions. Une seule exclusion en vigueur par étudiant et par programme.
- Seuls le chef de la scolarité et l'administrateur RÉINTÈGRENT l'étudiant (avec un motif) ; l'exclusion reste
  dans l'historique.
"""
from collections import defaultdict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Exclusion, Note
from .notes import regles, semestres_delibres


def seuils_exclusion():
    """{code du règlement: crédits de dettes au-delà desquels l'exclusion est proposée}."""
    return {code: r['seuil_exclusion'] for code, r in settings.GPED_NOTES.get('reglements', {}).items()
            if r.get('dettes') and r.get('seuil_exclusion')}


def exclusions_en_vigueur(etudiant_ids=None):
    qs = Exclusion.objects.filter(reintegre_le__isnull=True)
    if etudiant_ids is not None:
        qs = qs.filter(etudiant_id__in=list(etudiant_ids))
    return qs


def propositions(inscriptions):
    """
    Étudiants à exclure d'après leurs dettes, parmi les `inscriptions` (QuerySet, ex. celles que le profil voit) :
    [{'inscription': dernière inscription dans le programme, 'credits': crédits de dettes, 'seuil', 'matieres': n}],
    les plus endettés d'abord. Les étudiants déjà exclus de ce programme ne sont pas proposés.
    """
    seuils = seuils_exclusion()
    if not seuils:
        return []
    seuil_validation = regles()['seuil_validation']
    notes = list(Note.objects.filter(reglement__in=list(seuils), moyenne_finale__lt=seuil_validation,
                                     etudiant_id__in=inscriptions.values('etudiant_id'))
                 .select_related('matiere').only('etudiant_id', 'reglement', 'moyenne_finale', 'matiere__credit',
                                                 'matiere__filiere_id', 'matiere__session_id', 'matiere__semestre_id'))
    delibres = semestres_delibres([n.matiere for n in notes])
    dettes = defaultdict(lambda: {'credits': 0, 'matieres': 0, 'seuil': 0})
    for n in notes:
        m = n.matiere
        if (m.filiere_id, m.session_id, m.semestre_id) not in delibres:
            continue                                   # pas encore délibéré : pas encore une dette
        d = dettes[(n.etudiant_id, m.filiere_id)]
        d['credits'] += m.credit or 0
        d['matieres'] += 1
        d['seuil'] = max(d['seuil'], seuils[n.reglement])
    a_exclure = {cle: d for cle, d in dettes.items() if d['credits'] > d['seuil']}
    if not a_exclure:
        return []
    deja = set(exclusions_en_vigueur({e for e, _ in a_exclure}).values_list('etudiant_id', 'filiere_id'))
    dernieres = {}
    for i in (inscriptions.filter(etudiant_id__in={e for e, _ in a_exclure})
              .select_related('etudiant', 'session', 'licence', 'filiere', 'promotion').order_by('session__annee')):
        dernieres[(i.etudiant_id, i.filiere_id)] = i
    resultat = [{'inscription': dernieres[cle], **d} for cle, d in a_exclure.items()
                if cle not in deja and cle in dernieres]
    return sorted(resultat, key=lambda p: (-p['credits'], p['inscription'].etudiant.nom))


@transaction.atomic
def exclure(inscription, motif, details, date, utilisateur):
    if motif not in dict(Exclusion.MOTIFS):
        raise ValidationError("Choisissez le motif de l'exclusion.")
    if not inscription.filiere_id:
        raise ValidationError("Cette inscription n'a pas de programme.")
    if motif == Exclusion.AUTRE and not (details or '').strip():
        raise ValidationError("Précisez le motif de l'exclusion.")
    if exclusions_en_vigueur([inscription.etudiant_id]).filter(filiere_id=inscription.filiere_id).exists():
        raise ValidationError("Cet étudiant est déjà exclu de ce programme.")
    credits = None
    if motif == Exclusion.DETTES:
        credits = _credits_dettes(inscription)
    return Exclusion.objects.create(inscription=inscription, etudiant_id=inscription.etudiant_id,
                                    filiere_id=inscription.filiere_id, motif=motif, details=(details or '').strip(),
                                    credits_dettes=credits, date=date or timezone.localdate(), prononcee_par=utilisateur)


def _credits_dettes(inscription):
    """Crédits de dettes actuels de l'étudiant dans le programme (même sous le seuil), pour mémoire."""
    seuils = seuils_exclusion()
    if not seuils:
        return None
    notes = list(Note.objects.filter(etudiant_id=inscription.etudiant_id, matiere__filiere_id=inscription.filiere_id,
                                     reglement__in=list(seuils), moyenne_finale__lt=regles()['seuil_validation'])
                 .select_related('matiere'))
    delibres = semestres_delibres([n.matiere for n in notes])
    return sum(n.matiere.credit or 0 for n in notes
               if (n.matiere.filiere_id, n.matiere.session_id, n.matiere.semestre_id) in delibres)


def reintegrer(exclusion, motif, utilisateur):
    if not (motif or '').strip():
        raise ValidationError("Indiquez le motif de la réintégration.")
    if not exclusion.en_vigueur:
        raise ValidationError("Cet étudiant a déjà été réintégré.")
    exclusion.reintegre_le = timezone.now()
    exclusion.reintegre_par = utilisateur
    exclusion.motif_reintegration = motif.strip()
    exclusion.save(update_fields=['reintegre_le', 'reintegre_par', 'motif_reintegration'])
    return exclusion
