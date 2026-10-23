"""
Filtrage, tri et export de la liste des étudiants (inscriptions).

Toutes les requêtes passent par .values_list() : aucun objet modèle n'est
instancié, ce qui garde la liste rapide même avec des dizaines de milliers de lignes.
"""
from django.db.models import Q

from .models import Session
from .models import Inscription
from .roles import inscriptions_visibles

# Colonnes renvoyées au tableau : (clé, champ ORM)
COLONNES_TABLE = [
    ('id', 'id'),
    ('matricule', 'etudiant__matricule'),
    ('prenom', 'etudiant__prenom'),
    ('nom', 'etudiant__nom'),
    ('sexe', 'etudiant__sexe'),
    ('telephone', 'etudiant__telephone'),
    ('licence', 'licence__nom'),
    ('semestre', 'semestre__abrege'),
    ('programme', 'filiere__nom'),
    ('promotion', 'promotion__nom'),
    ('redouble', 'redouble'),
    ('cycle', 'cycle'),
    ('preinscrit', 'etudiant__preinscrit'),
]

CYCLE_LIBELLES = dict(Inscription.CYCLE_CHOICES)

# Filtre « Statut » de la liste
STATUTS = {
    'inscrit': {'etudiant__preinscrit': False},
    'preinscrit': {'etudiant__preinscrit': True},
    'redoublant': {'redouble': True},
    'passant': {'redouble': False},
}

# Tri autorisé : clé envoyée par le navigateur -> champ ORM
TRIS = {
    'cycle': 'cycle',
    'matricule': 'etudiant__matricule',
    'prenom': 'etudiant__prenom',
    'nom': 'etudiant__nom',
    'sexe': 'etudiant__sexe',
    'licence': 'licence__nom',
    'semestre': 'semestre__abrege',
    'programme': 'filiere__nom',
    'promotion': 'promotion__nom',
}

# Colonnes de l'export (même ordre que le modèle d'import, + inscription)
COLONNES_EXPORT = [
    ('Matricule', 'etudiant__matricule'),
    ('Prénom', 'etudiant__prenom'),
    ('Nom', 'etudiant__nom'),
    ('Sexe', 'etudiant__sexe'),
    ('Date de naissance', 'etudiant__date_de_naissance'),
    ('Lieu de naissance', 'etudiant__lieu_de_naissance'),
    ('Nationalité', 'etudiant__nationalite'),
    ('Téléphone', 'etudiant__telephone'),
    ('E-mail', 'etudiant__email'),
    ('Père', 'etudiant__pere'),
    ('Mère', 'etudiant__mere'),
    ('Tuteur', 'etudiant__tuteur'),
    ('Contact tuteur', 'etudiant__contact_de_tuteur'),
    ('Adresse', 'etudiant__adresse'),
    ('INE', 'etudiant__ine'),
    ('PV Bac', 'etudiant__pvbac'),
    ("Année d'obtention bac", 'etudiant__annee_obtention_bac'),
    ("Année d'orientation", 'etudiant__annee_orientation'),
    ('Option du bac', 'etudiant__option_de_bac'),
    ('Moyenne au bac', 'etudiant__moyenne_au_bac'),
    ("École d'origine", 'etudiant__ecole_origine'),
    ('Centre du bac', 'etudiant__centre_de_bac'),
    ('Session', 'session__annee'),
    ('Cycle', 'cycle'),
    ('Licence', 'licence__nom'),
    ('Semestre', 'semestre__nom'),
    ('Programme', 'filiere__nom'),
    ('Promotion', 'promotion__nom'),
    ('Redoublant', 'redouble'),
    ('Préinscrit', 'etudiant__preinscrit'),
    ("Date d'inscription", 'date_inscription'),
]


def session_par_defaut():
    session = Session.objects.filter(en_cours=True).order_by('-annee').first()
    return session or Session.objects.order_by('-annee').first()


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def filtrer(profil, params):
    """
    Applique le périmètre du profil puis les filtres de la requête.
    `params` : QueryDict (GET). Renvoie (queryset, session_id).
    """
    qs = inscriptions_visibles(profil)

    session_id = _entier(params.get('session'))
    if session_id is None and params.get('session') != 'toutes':
        session = session_par_defaut()
        session_id = session.id if session else None
    if session_id:
        qs = qs.filter(session_id=session_id)

    for cle, champ in (('programme', 'filiere_id'), ('licence', 'licence_id'),
                       ('semestre', 'semestre_id'), ('promotion', 'promotion_id')):
        valeur = _entier(params.get(cle))
        if valeur:
            qs = qs.filter(**{champ: valeur})

    if params.get('sexe') in ('M', 'F'):
        qs = qs.filter(etudiant__sexe=params['sexe'])
    if params.get('redouble') in ('0', '1'):
        qs = qs.filter(redouble=params['redouble'] == '1')
    if params.get('preinscrit') in ('0', '1'):
        qs = qs.filter(etudiant__preinscrit=params['preinscrit'] == '1')
    statut = params.get('statut')
    if statut in STATUTS:
        qs = qs.filter(**STATUTS[statut])
    # Liste des étudiants (et son export) : inscrits seulement ; les préinscrits ont leur page « Préinscriptions »
    if statut != 'preinscrit' and params.get('preinscrit') != '1':
        qs = qs.filter(etudiant__preinscrit=False)
    if params.get('cycle') in CYCLE_LIBELLES:
        qs = qs.filter(cycle=params['cycle'])

    for terme in (params.get('q') or '').split()[:5]:
        qs = qs.filter(
            Q(etudiant__matricule__icontains=terme)
            | Q(etudiant__nom__icontains=terme)
            | Q(etudiant__prenom__icontains=terme)
            | Q(etudiant__telephone__icontains=terme)
            | Q(etudiant__ine__icontains=terme)
        )
    return qs, session_id


def trier(qs, params):
    champ = TRIS.get(params.get('tri'), 'etudiant__nom')
    prefixe = '-' if params.get('ordre') == 'desc' else ''
    # 'id' en dernier garantit un ordre stable entre deux pages
    return qs.order_by(prefixe + champ, prefixe + 'etudiant__prenom', 'id')
