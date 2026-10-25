from pathlib import Path

from django.conf import settings

from .roles import get_profil, inscriptions_visibles

DOSSIER_STATIQUE = Path(__file__).resolve().parent / 'static' / 'pfe'
_empreinte = None


def empreinte_statique():
    """
    Ajoutée aux URL des CSS/JS/images (?v=…) : change dès qu'un fichier est modifié, ce qui force
    les navigateurs à recharger (plus besoin d'augmenter un numéro à la main). Calculée une fois
    au démarrage ; en développement (DEBUG), à chaque page, pour suivre les modifications.
    """
    global _empreinte
    if _empreinte is None or settings.DEBUG:
        dates = [f.stat().st_mtime for f in DOSSIER_STATIQUE.rglob('*') if f.is_file()]
        _empreinte = format(int(max(dates, default=0)), 'x')
    return _empreinte


def gped(request):
    contexte = {'version_statique': empreinte_statique(), 'version_gped': settings.GPED_VERSION,
                'en_developpement': settings.DEBUG}
    if getattr(request, 'user', None) and request.user.is_authenticated:
        profil = get_profil(request)
        contexte['profil'] = profil
        # Photo de l'utilisateur connecté (en-tête, menu, Mon compte) : personnel, sinon étudiant
        from .photos import url_photo
        contexte['photo_compte'] = (url_photo(profil.personnel)
                                    or url_photo(getattr(request.user, 'profil_etudiant', None)))
        if profil.a_acces:
            contexte['nb_preinscrits'] = _nb_preinscrits(profil)
        if profil.est_scolarite:
            from .views_demandes import nb_demandes_en_attente
            contexte['nb_demandes'] = nb_demandes_en_attente()
    return contexte


def _nb_preinscrits(profil):
    """Préinscrits en attente (session en cours, périmètre du profil) : compteur du menu."""
    from .liste import session_par_defaut
    qs = inscriptions_visibles(profil).filter(etudiant__preinscrit=True)
    session = session_par_defaut()
    if session:
        qs = qs.filter(session=session)
    return qs.count()
