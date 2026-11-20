"""Guides d'utilisation : ouvrir son guide (tout compte qui en a un), déposer les guides (super utilisateur)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from . import guides
from .models import GuideUtilisation
from .roles import acces_requis, get_profil
from .views_personnel import superuser_requis


def _reponse_pdf(guide):
    nom = slugify(f"guide gePed {guide.get_public_display()} {guide.date:%Y-%m-%d}") + '.pdf'
    reponse = FileResponse(guides.ouvrir(guide), content_type='application/pdf', filename=nom)
    reponse['Content-Disposition'] = f'inline; filename="{nom}"'
    reponse['X-Content-Type-Options'] = 'nosniff'
    reponse['Cache-Control'] = 'private, no-store'
    return reponse


@login_required
@acces_requis
def guide_ouvrir(request):
    """Le guide du compte (dernière version) ; l'ouverture fait disparaître le bandeau « Nouveautés »."""
    guide = guides.guide_courant(guides.public_du_profil(get_profil(request)))
    if guide is None:
        messages.info(request, "Aucun guide d'utilisation n'est encore disponible pour votre compte.")
        return redirect('tableau_de_bord')
    guides.noter_lecture(request.user, guide)
    return _reponse_pdf(guide)


@login_required
@superuser_requis
def guide_version(request, pk):
    """Super utilisateur : n'importe quelle version déposée (vérification avant diffusion)."""
    return _reponse_pdf(get_object_or_404(GuideUtilisation, pk=pk))


@login_required
@superuser_requis
def guides_gestion(request):
    """Déposer une nouvelle version d'un guide (PDF + nouveautés) ; historique des versions."""
    from .notes import verifier_pdf
    erreur = ''
    if request.method == 'POST':
        public = request.POST.get('public')
        fichier = request.FILES.get('fichier')
        nouveautes = (request.POST.get('nouveautes') or '').strip()
        if public not in dict(GuideUtilisation.PUBLICS):
            erreur = "Choisissez les comptes concernés."
        else:
            try:
                verifier_pdf(fichier, 'Le guide')
            except ValueError as e:
                erreur = str(e)
        if not erreur:
            from django.utils import timezone
            from .models import stockage_prive
            nom = stockage_prive().save(f"guides/{timezone.now():%Y}/guide_{public}_{timezone.now():%Y%m%d%H%M%S}.pdf",
                                        fichier)
            GuideUtilisation.objects.create(public=public, fichier=nom, nouveautes=nouveautes,
                                            taille=fichier.size, depose_par=request.user)
            messages.success(request, f"Guide « {dict(GuideUtilisation.PUBLICS)[public]} » déposé : les utilisateurs "
                                      "concernés verront ses nouveautés jusqu'à ce qu'ils l'ouvrent.")
            return redirect('guides_gestion')
    versions = list(GuideUtilisation.objects.select_related('depose_par'))
    publics = []
    for code, libelle in GuideUtilisation.PUBLICS:
        liste = [v for v in versions if v.public == code]
        publics.append({'code': code, 'libelle': libelle, 'courant': liste[0] if liste else None,
                        'anciennes': liste[1:]})
    for p in publics:
        if p['courant']:
            p['lectures'] = p['courant'].lectures.count()
    return render(request, 'pfe/guides_gestion.html', {
        'publics': publics, 'erreur': erreur, 'choix': GuideUtilisation.PUBLICS, 'actif': 'guides',
        'nouveautes': request.POST.get('nouveautes', '') if erreur else '', 'public_choisi': request.POST.get('public'),
    })
