"""
Contenu des relevés de notes (calculé à chaque impression, d'après les notes enregistrées).

  - Relevé semestriel : les matières DONT LE SEMESTRE est celui demandé, dans le programme,
    la session et la licence de l'inscription de l'étudiant (pas le semestre de l'inscription).
  - Relevé annuel : toutes les matières de la licence de l'inscription (S1 et S2…).
  - Extrait du livret : tout le parcours de l'étudiant dans UN programme, une année par licence
    (licence redoublée : la dernière année seulement), moyenne du cycle, soutenance du programme,
    moyenne générale (= moyenne du cycle s'il n'y a pas de soutenance). Le Tronc commun a donc son
    propre extrait, et le programme d'orientation le sien.

Moyennes pondérées par les crédits (6 par défaut), tronquées à 2 décimales ; une matière sans
note compte 0 et est signalée. « PROVISOIRE » tant qu'un semestre n'est pas délibéré : une session
antérieure à la session en cours est considérée comme délibérée (notes importées des anciennes années).
"""
from django.db.models import Q

from .models import Deliberation, Inscription, Matiere, Note, ReleveNote, Session, Soutenance
from .notes import _agreger, annees_retenues, arrondi, est_validee, lettre, mention, moyenne_generale


def session_courante():
    return Session.objects.filter(en_cours=True).first() or Session.objects.order_by('-annee').first()


def _definitif(filiere_id, session, semestre_ids):
    """Session antérieure à la session en cours, ou tous les semestres délibérés."""
    courante = session_courante()
    if courante and session.annee < courante.annee:
        return True
    if not semestre_ids:
        return False
    delibres = set(Deliberation.objects.filter(filiere_id=filiere_id, session=session, semestre_id__in=semestre_ids)
                   .values_list('semestre_id', flat=True))
    return set(semestre_ids) <= delibres


def _observation(note):
    if note is None:
        return 'Note manquante'
    if not est_validee(note.moyenne_finale):
        return 'Non validée'
    return 'Validée en session' if note.par_session else 'Validée'


def bloc(inscription, semestre=None):
    """Matières, notes et moyenne d'une inscription (un semestre, ou toute l'année)."""
    matieres = Matiere.objects.filter(filiere_id=inscription.filiere_id, session_id=inscription.session_id,
                                      licence_id=inscription.licence_id).select_related('semestre')
    if semestre is not None:
        matieres = matieres.filter(semestre=semestre)
    matieres = sorted(matieres, key=lambda m: (m.semestre_id, m.nom.lower()))
    notes = {n.matiere_id: n for n in Note.objects.filter(etudiant_id=inscription.etudiant_id,
                                                           matiere__in=[m.pk for m in matieres])}
    lignes = []
    for m in matieres:
        n = notes.get(m.pk)
        valeur = n.moyenne_finale if n else None
        lignes.append({'matiere': m.nom, 'credit': m.credit, 'semestre': m.semestre.abrege or m.semestre.nom,
                       'note': valeur, 'lettre': lettre(valeur), 'observation': _observation(n),
                       'validee': est_validee(valeur)})
    resultat = _agreger(matieres, {(inscription.etudiant_id, k): v.moyenne_finale for k, v in notes.items()
                                   if v.moyenne_finale is not None}, inscription.etudiant_id)
    semestres = sorted({m.semestre_id for m in matieres})
    return {'inscription': inscription, 'lignes': lignes, **resultat,
            'semestres': [m.semestre for m in {m.semestre_id: m for m in matieres}.values()],
            'definitif': _definitif(inscription.filiere_id, inscription.session, semestres)}


def inscription_du_releve(releve):
    return (Inscription.objects.filter(etudiant_id=releve.etudiant_id, filiere_id=releve.filiere_id,
                                       session_id=releve.session_id, licence_id=releve.licence_id)
            .select_related('session', 'licence', 'filiere', 'promotion').first())


def contenu(releve):
    """Tout ce que le PDF imprime, pour les trois types de relevé."""
    if releve.type == ReleveNote.EXTRAIT:
        return contenu_extrait(releve)
    insc = inscription_du_releve(releve)
    if insc is None:
        raise ValueError("L'inscription de ce relevé n'existe plus.")
    if releve.type == ReleveNote.SEMESTRE:
        b = bloc(insc, releve.semestre)
        return {'titre': 'RELEVÉ DE NOTES', 'niveau': releve.semestre.nom, 'libelle_moyenne': 'Moyenne semestrielle',
                'session': insc.session.annee, 'blocs': [b], 'definitif': b['definitif'], 'promotion': insc.promotion,
                'moyenne': b['moyenne'], 'mention': b['mention']}
    b = bloc(insc)
    abreges = ' et '.join(s.abrege or s.nom for s in sorted(b['semestres'], key=lambda s: s.pk))
    return {'titre': 'RELEVÉ DE NOTES', 'niveau': f"{insc.licence.nom}" + (f" ({abreges})" if abreges else ''),
            'libelle_moyenne': 'Moyenne annuelle', 'session': insc.session.annee, 'blocs': [b],
            'definitif': b['definitif'], 'promotion': insc.promotion, 'moyenne': b['moyenne'], 'mention': b['mention']}


def contenu_extrait(releve):
    inscriptions = list(Inscription.objects.filter(etudiant_id=releve.etudiant_id, filiere_id=releve.filiere_id)
                        .select_related('session', 'licence', 'promotion'))
    blocs_tous = [(i.session.annee, i, bloc(i)) for i in inscriptions]
    retenues = annees_retenues(blocs_tous)                        # une année par licence : la dernière
    blocs = sorted((b for _, b in retenues.values()), key=lambda b: b['inscription'].session.annee)
    blocs = [b for b in blocs if b['lignes']]
    moyennes = [b['moyenne'] for b in blocs if b['moyenne'] is not None]
    moyenne_cycle = arrondi(sum(moyennes) / len(moyennes)) if moyennes else None
    s = (Soutenance.objects.filter(etudiant_id=releve.etudiant_id, filiere_id=releve.filiere_id)
         .order_by('-session__annee').first())
    note_soutenance = s.note_finale if s else None
    generale = moyenne_generale(moyenne_cycle, note_soutenance)
    derniere = blocs[-1]['inscription'] if blocs else None
    return {'titre': 'EXTRAIT DU LIVRET UNIVERSITAIRE', 'niveau': '', 'blocs': blocs,
            'definitif': all(b['definitif'] for b in blocs) if blocs else False,
            'promotion': derniere.promotion if derniere else None, 'session': '',
            'moyenne_cycle': moyenne_cycle, 'soutenance': note_soutenance, 'soutenance_session': s.par_session if s else False,
            'moyenne': generale, 'mention': mention(generale), 'libelle_moyenne': 'Moyenne générale',
            'credits_valides': sum(b['credits_valides'] for b in blocs),
            'credits_total': sum(b['credits_total'] for b in blocs)}


def parcours(etudiant, profil=None):
    """
    Ce que l'on peut délivrer à un étudiant : pour chaque inscription (avec programme), les semestres
    qui ont des matières, le relevé annuel ; et un extrait par programme.
    """
    inscriptions = list(Inscription.objects.filter(etudiant=etudiant, filiere__isnull=False)
                        .select_related('session', 'licence', 'filiere').order_by('session__annee', 'licence_id'))
    triplets = {(i.filiere_id, i.session_id, i.licence_id) for i in inscriptions}
    semestres = {}
    if triplets:
        for m in (Matiere.objects.filter(filiere_id__in={t[0] for t in triplets},
                                         session_id__in={t[1] for t in triplets},
                                         licence_id__in={t[2] for t in triplets}).select_related('semestre')):
            cle = (m.filiere_id, m.session_id, m.licence_id)
            if cle in triplets:
                semestres.setdefault(cle, {})[m.semestre_id] = m.semestre
    for i in inscriptions:
        i.semestres_releve = sorted(semestres.get((i.filiere_id, i.session_id, i.licence_id), {}).values(),
                                    key=lambda s: s.pk)
    programmes = []
    for i in inscriptions:
        if i.filiere not in programmes:
            programmes.append(i.filiere)
    return inscriptions, programmes


def a_des_notes(etudiant, filiere, session=None, licence=None, semestre=None):
    """Au moins une note (moyenne finale) dans le périmètre du relevé : sinon, pas de relevé."""
    qs = Note.objects.filter(etudiant=etudiant, moyenne_finale__isnull=False, matiere__filiere=filiere)
    if session is not None:
        qs = qs.filter(matiere__session=session, matiere__licence=licence)
    if semestre is not None:
        qs = qs.filter(matiere__semestre=semestre)
    return qs.exists()


def perimetres_notes(etudiant):
    """{(programme, session, licence, semestre)} où l'étudiant a au moins une note (boutons du parcours)."""
    return set(Note.objects.filter(etudiant=etudiant, moyenne_finale__isnull=False)
               .values_list('matiere__filiere_id', 'matiere__session_id', 'matiere__licence_id', 'matiere__semestre_id'))


def releve_existant(type_, etudiant, filiere, session=None, licence=None, semestre=None):
    """Un relevé déjà délivré pour le même objet : on le réimprime (même numéro)."""
    return ReleveNote.objects.filter(type=type_, etudiant=etudiant, filiere=filiere, session=session,
                                     licence=licence, semestre=semestre).first()


def infos_publiques_releve(releve):
    """Page publique (QR) : titre et informations IMPRIMÉES sur le relevé, pour comparaison."""
    from .attestations import _nom_programme
    c = contenu(releve)
    e = releve.etudiant
    if releve.type == ReleveNote.SEMESTRE:
        titre = f"Relevé de notes · {releve.semestre.nom}"
    elif releve.type == ReleveNote.ANNUEL:
        titre = f"Relevé de notes · {c['niveau']}"
    else:
        titre = "Extrait du livret universitaire"
    infos = [('Étudiant(e)', f"{e.prenom.upper()} {e.nom.upper()}"), ('Matricule', e.matricule or '—'),
             ('Département', _nom_programme(releve.filiere))]
    if c.get('session'):
        infos.append(('Année universitaire', c['session']))
    if releve.type == ReleveNote.EXTRAIT:
        infos.append(('Années', ', '.join(f"{b['inscription'].licence.nom} ({b['inscription'].session.annee})"
                                          for b in c['blocs']) or '—'))
    from .pdf_releves import fmt
    infos += [(c['libelle_moyenne'], f"{fmt(c['moyenne'])}/10"), ('Mention', c['mention'] or '—'),
              ('Statut', 'Définitif' if c['definitif'] else 'Provisoire (notes non encore délibérées)')]
    return titre, infos


def recherche(q):
    qs = Q()
    for terme in q.split()[:4]:
        qs &= (Q(matricule__icontains=terme) | Q(nom__icontains=terme) | Q(prenom__icontains=terme)
               | Q(ine__icontains=terme))
    return qs
