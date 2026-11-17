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

Arrêté 877 (Tronc commun et cycle Ingénieur, voir settings.GPED_NOTES) : chaque matière montre la note de
session normale, le rattrapage et la note retenue (rattrapage plafonné à 5), la notation A+ … E et l'état
(Validée, Validée au rattrapage, À rattraper, Non validée, Dette) ; le relevé semestriel ajoute la
moyenne générale cumulative du programme (CPC pour le Tronc commun, cycle ingénieur sinon) et les
crédits cumulés, le relevé annuel n'a que la moyenne et les crédits de l'année ; l'extrait du livret
donne la moyenne cumulative, sans soutenance ajoutée à part.
"""
import re

from django.db.models import Q

from .models import Deliberation, Inscription, Matiere, Note, ReleveNote, Session, Soutenance
from .notes import (_agreger, annees_retenues, arrondi, code_inscription, est_validee, lettre, mention,
                    moyenne_generale, observation, regles)


def session_courante():
    return Session.objects.filter(en_cours=True).first() or Session.objects.order_by('-annee').first()


def _semestres_definitifs(filiere_id, session, semestre_ids):
    """Semestres définitifs : tous si la session est antérieure à la session en cours, sinon les délibérés."""
    courante = session_courante()
    if courante and session.annee < courante.annee:
        return set(semestre_ids)
    if not semestre_ids:
        return set()
    return set(Deliberation.objects.filter(filiere_id=filiere_id, session=session, semestre_id__in=semestre_ids)
               .values_list('semestre_id', flat=True))


def _definitif(filiere_id, session, semestre_ids):
    """Session antérieure à la session en cours, ou tous les semestres délibérés."""
    courante = session_courante()
    if courante and session.annee < courante.annee:
        return True
    if not semestre_ids:
        return False
    return set(semestre_ids) <= _semestres_definitifs(filiere_id, session, semestre_ids)


def _numero(semestre):
    """« SEMESTRE 3 » → 3 (ordre des semestres) ; sinon son identifiant."""
    m = re.search(r'(\d+)', semestre.nom or '')
    return int(m.group(1)) if m else semestre.pk


def bloc(inscription, semestre=None):
    """Matières, notes et moyenne d'une inscription (un semestre, ou toute l'année)."""
    code = code_inscription(inscription)
    matieres = Matiere.objects.filter(filiere_id=inscription.filiere_id, session_id=inscription.session_id,
                                      licence_id=inscription.licence_id).select_related('semestre')
    if semestre is not None:
        matieres = matieres.filter(semestre=semestre)
    matieres = sorted(matieres, key=lambda m: (m.semestre_id, m.nom.lower()))
    notes = {n.matiere_id: n for n in Note.objects.filter(etudiant_id=inscription.etudiant_id,
                                                           matiere__in=[m.pk for m in matieres])}
    semestres = sorted({m.semestre_id for m in matieres})
    definitifs = _semestres_definitifs(inscription.filiere_id, inscription.session, semestres)
    lignes = []
    for m in matieres:
        n = notes.get(m.pk)
        valeur = n.moyenne_finale if n else None
        lignes.append({'matiere': m.nom, 'credit': m.credit, 'semestre': m.semestre.abrege or m.semestre.nom,
                       'note': valeur, 'lettre': lettre(valeur, code),
                       'normale': n.moyenne if n else None, 'rattrapage': n.rattrapage if n else None,
                       'observation': observation(n, m.semestre_id in definitifs),
                       'validee': est_validee(valeur)})
    resultat = _agreger(matieres, {(inscription.etudiant_id, k): v.moyenne_finale for k, v in notes.items()
                                   if v.moyenne_finale is not None}, inscription.etudiant_id, code)
    return {'inscription': inscription, 'lignes': lignes, **resultat, 'reglement': code,
            'detaille': bool(regles(code).get('dettes')),
            'semestres': [m.semestre for m in {m.semestre_id: m for m in matieres}.values()],
            'definitif': _definitif(inscription.filiere_id, inscription.session, semestres)}


def libelle_cumul(inscription):
    """Arrêté 877 : la moyenne cumulative du Tronc commun est celle du CPC, sinon celle du cycle ingénieur."""
    if inscription.cycle == 'Tronc commun':
        return 'Moyenne générale du CPC'
    return 'Moyenne générale du cycle'


def cumul(inscription, semestre=None):
    """
    Arrêté 877 : moyenne générale cumulative (pondérée par les crédits) et crédits cumulés de l'étudiant
    dans le programme de l'inscription, jusqu'à cette inscription (et ce semestre) compris ; une année
    par niveau (redoublement : la dernière, jusqu'à cette session).
    """
    code = code_inscription(inscription)
    inscriptions = list(Inscription.objects.filter(etudiant_id=inscription.etudiant_id,
                                                   filiere_id=inscription.filiere_id, conge__isnull=True,
                                                   session__annee__lte=inscription.session.annee)
                        .select_related('session'))
    if inscription.pk not in {i.pk for i in inscriptions}:
        inscriptions.append(inscription)
    retenues = [i for i, _ in annees_retenues([(i.session.annee, i, None) for i in inscriptions]).values()]
    matieres = []
    for i in retenues:
        ms = list(Matiere.objects.filter(filiere_id=i.filiere_id, session_id=i.session_id,
                                         licence_id=i.licence_id).select_related('semestre'))
        if i.pk == inscription.pk and semestre is not None:
            ms = [m for m in ms if _numero(m.semestre) <= _numero(semestre)]
        matieres.extend(ms)
    notes = {(inscription.etudiant_id, mat): v for mat, v in Note.objects.filter(
        etudiant_id=inscription.etudiant_id, matiere__in=[m.pk for m in matieres],
        moyenne_finale__isnull=False).values_list('matiere_id', 'moyenne_finale')}
    r = _agreger(matieres, notes, inscription.etudiant_id, code)
    return {'libelle': libelle_cumul(inscription), 'moyenne': r['moyenne'], 'mention': r['mention'],
            'credits_valides': r['credits_valides'], 'credits_total': r['credits_total']}


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
                'moyenne': b['moyenne'], 'mention': b['mention'],
                'cumul': cumul(insc, releve.semestre) if b['detaille'] else None}
    b = bloc(insc)
    abreges = ' et '.join(s.abrege or s.nom for s in sorted(b['semestres'], key=lambda s: s.pk))
    return {'titre': 'RELEVÉ DE NOTES', 'niveau': f"{insc.licence.nom}" + (f" ({abreges})" if abreges else ''),
            'libelle_moyenne': 'Moyenne annuelle', 'session': insc.session.annee, 'blocs': [b],
            'definitif': b['definitif'], 'promotion': insc.promotion, 'moyenne': b['moyenne'], 'mention': b['mention'],
            'cumul': None}                         # relevé annuel : moyenne et crédits de l'année seulement


def contenu_extrait(releve):
    inscriptions = list(Inscription.objects.filter(etudiant_id=releve.etudiant_id, filiere_id=releve.filiere_id)
                        .select_related('session', 'licence', 'promotion'))
    blocs_tous = [(i.session.annee, i, bloc(i)) for i in inscriptions]
    retenues = annees_retenues(blocs_tous)                        # une année par licence : la dernière
    blocs = sorted((b for _, b in retenues.values()), key=lambda b: b['inscription'].session.annee)
    blocs = [b for b in blocs if b['lignes']]
    derniere = blocs[-1]['inscription'] if blocs else None
    s = (Soutenance.objects.filter(etudiant_id=releve.etudiant_id, filiere_id=releve.filiere_id)
         .order_by('-session__annee').first())
    note_soutenance = s.note_finale if s else None
    code = code_inscription(derniere) if derniere else ''
    libelle_cycle = 'Moyenne du cycle'
    if regles(code)['moyenne_cycle'] == 'cumulative':
        # Arrêté 877 : moyenne cumulative pondérée par les crédits ; le PFE comptera comme une matière de S10
        c = cumul(derniere)
        moyenne_cycle = generale = c['moyenne']
        libelle_cycle, note_soutenance = c['libelle'], None
    else:
        moyennes = [b['moyenne'] for b in blocs if b['moyenne'] is not None]
        moyenne_cycle = arrondi(sum(moyennes) / len(moyennes)) if moyennes else None
        generale = moyenne_generale(moyenne_cycle, note_soutenance)
    return {'titre': 'EXTRAIT DU LIVRET UNIVERSITAIRE', 'niveau': '', 'blocs': blocs,
            'definitif': all(b['definitif'] for b in blocs) if blocs else False,
            'promotion': derniere.promotion if derniere else None, 'session': '',
            'moyenne_cycle': moyenne_cycle, 'libelle_cycle': libelle_cycle, 'reglement': code,
            'soutenance': note_soutenance, 'soutenance_session': s.par_session if s else False,
            'moyenne': generale, 'mention': mention(generale, code), 'libelle_moyenne': 'Moyenne générale',
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
    from .attestations import _nom_departement
    c = contenu(releve)
    e = releve.etudiant
    if releve.type == ReleveNote.SEMESTRE:
        titre = f"Relevé de notes · {releve.semestre.nom}"
    elif releve.type == ReleveNote.ANNUEL:
        titre = f"Relevé de notes · {c['niveau']}"
    else:
        titre = "Extrait du livret universitaire"
    infos = [('Étudiant(e)', f"{e.prenom.upper()} {e.nom.upper()}"), ('Matricule', e.matricule or '—'),
             ('Département', _nom_departement(releve.filiere)), ('Programme', releve.filiere.nom)]
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
