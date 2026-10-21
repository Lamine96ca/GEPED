"""
Gestion du personnel : fiches, comptes, invitations. RÉSERVÉE AUX SUPER UTILISATEURS
(menu « Personnel » visible seulement pour eux). Personne ne peut désactiver son propre compte.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST

from .comptes import envoyer_lien, jeton_invitation, statut
from .forms import NouveauMotDePasseForm, PersonnelForm
from .models import Personnel, Service
from .roles import _calculer_profil

STATUTS = [('actif', 'Actif'), ('invite', 'Invitation en attente'), ('desactive', 'Désactivé')]


def superuser_requis(vue):
    @wraps(vue)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("La gestion du personnel est réservée aux super utilisateurs.")
        return vue(request, *args, **kwargs)
    return wrapper


def _peut_gerer(request, personnel):
    return request.user.is_superuser


def _envoyer(request, user, invitation=True):
    try:
        adresse = envoyer_lien(request, user, invitation=invitation)
    except Exception as e:                                   # SMTP indisponible, adresse vide…
        messages.error(request, f"L'e-mail n'a pas pu être envoyé ({e}). Réessayez avec « Renvoyer l'invitation ».")
        return False
    messages.success(request, (f"E-mail d'invitation envoyé à {adresse}." if invitation
                               else f"Lien de réinitialisation du mot de passe envoyé à {adresse}."))
    return True


@login_required
@superuser_requis
def personnel_liste(request):
    g = request.GET
    q = (g.get('q') or '').strip()
    qs = Personnel.objects.select_related('utilisateur').prefetch_related('service')
    if q:
        for terme in q.split()[:4]:
            qs = qs.filter(Q(prenom__icontains=terme) | Q(nom__icontains=terme) | Q(email__icontains=terme)
                           | Q(telephone__icontains=terme) | Q(fonc__icontains=terme)
                           | Q(utilisateur__username__icontains=terme))
    service = int(g['service']) if str(g.get('service', '')).isdigit() else None
    if service:
        qs = qs.filter(service__id=service)
    statut_choisi = g.get('statut') if g.get('statut') in dict(STATUTS) else ''
    if statut_choisi == 'desactive':
        qs = qs.filter(utilisateur__is_active=False)
    elif statut_choisi:
        qs = qs.filter(utilisateur__is_active=True)
    personnels = list(qs.order_by('nom', 'prenom').distinct())
    for p in personnels:
        p.statut, p.statut_libelle = statut(p.utilisateur)
        p.role = _calculer_profil(p.utilisateur).libelle
        p.gerable = _peut_gerer(request, p)
    if statut_choisi in ('actif', 'invite'):
        personnels = [p for p in personnels if p.statut == statut_choisi]
    page = Paginator(personnels, 25).get_page(g.get('page'))
    params = g.copy()
    params.pop('page', None)
    return render(request, 'pfe/personnel_liste.html', {
        'page': page, 'q': q, 'filtres': {'service': service, 'statut': statut_choisi}, 'statuts': STATUTS,
        'services': Service.objects.order_by('-programme', 'nom'), 'params': params.urlencode(),
        'nb_total': Personnel.objects.count(), 'actif': 'personnel',
    })


def _formulaire(request, personnel=None):
    if personnel is not None and not _peut_gerer(request, personnel):
        raise PermissionDenied("Ce compte (scolarité ou administrateur) ne peut être modifié que par un super utilisateur.")
    if request.method == 'POST':
        form = PersonnelForm(request.POST, instance=personnel, editeur=request.user)
        if form.is_valid():
            p = form.save()
            if personnel is None:
                messages.success(request, f"Compte créé pour {p.prenom} {p.nom} (identifiant : "
                                          f"{p.utilisateur.username}).")
                if form.cleaned_data.get('envoyer_invitation'):
                    _envoyer(request, p.utilisateur)
                else:
                    messages.info(request, "Aucun e-mail envoyé : utilisez « Envoyer l'invitation » quand vous "
                                           "serez prêt.")
            else:
                messages.success(request, f"Fiche de {p.prenom} {p.nom} enregistrée.")
            return redirect('personnel_liste')
    else:
        form = PersonnelForm(instance=personnel, editeur=request.user)
    contexte = {'form': form, 'personnel': personnel, 'actif': 'personnel',
                'titre': f"{personnel.prenom} {personnel.nom}" if personnel else 'Nouveau membre du personnel'}
    if personnel is not None:
        contexte['statut'], contexte['statut_libelle'] = statut(personnel.utilisateur)
        contexte['role'] = _calculer_profil(personnel.utilisateur).libelle
        contexte['soi_meme'] = personnel.utilisateur_id == request.user.pk
    return render(request, 'pfe/personnel_form.html', contexte)


@login_required
@superuser_requis
def personnel_creer(request):
    return _formulaire(request)


@login_required
@superuser_requis
def personnel_modifier(request, pk):
    return _formulaire(request, get_object_or_404(Personnel.objects.select_related('utilisateur'), pk=pk))


@login_required
@superuser_requis
@require_POST
def personnel_action(request, pk):
    """Renvoyer l'invitation, envoyer un lien de réinitialisation, désactiver ou réactiver le compte."""
    p = get_object_or_404(Personnel.objects.select_related('utilisateur'), pk=pk)
    if not _peut_gerer(request, p):
        raise PermissionDenied("Ce compte ne peut être géré que par un super utilisateur.")
    u, action = p.utilisateur, request.POST.get('action')
    if action in ('desactiver', 'reactiver') and u.pk == request.user.pk:
        messages.error(request, "Vous ne pouvez pas désactiver votre propre compte.")
    elif action == 'desactiver':
        u.is_active = False
        u.save(update_fields=['is_active'])
        messages.success(request, f"Compte de {p.prenom} {p.nom} désactivé : il ne peut plus se connecter.")
    elif action == 'reactiver':
        u.is_active = True
        u.save(update_fields=['is_active'])
        messages.success(request, f"Compte de {p.prenom} {p.nom} réactivé.")
    elif action in ('invitation', 'reinitialiser'):
        if not u.is_active:
            messages.error(request, "Ce compte est désactivé : réactivez-le avant d'envoyer un e-mail.")
        else:
            _envoyer(request, u, invitation=(action == 'invitation'))
    return redirect(request.POST.get('suivant') if str(request.POST.get('suivant', '')).startswith('/')
                    else reverse('personnel_liste'))


class ActivationCompte(auth_views.PasswordResetConfirmView):
    """
    Lien de l'e-mail d'invitation : l'utilisateur choisit son mot de passe, puis il est connecté
    et arrive sur son tableau de bord (droits selon ses services).
    """
    token_generator = jeton_invitation
    form_class = NouveauMotDePasseForm
    template_name = 'pfe/compte_activer.html'
    post_reset_login = True
    post_reset_login_backend = 'django.contrib.auth.backends.ModelBackend'
    success_url = reverse_lazy('tableau_de_bord')

    def form_valid(self, form):
        reponse = super().form_valid(form)
        messages.success(self.request, "Bienvenue sur gePed : votre compte est activé.")
        return reponse
