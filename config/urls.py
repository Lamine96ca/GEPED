from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path

from pfe.forms import MotDePasseOublieForm, NouveauMotDePasseForm
from pfe.views import media_protege
from pfe import views_espace

urlpatterns = [
    path('django-admin/', admin.site.urls),
    # Espace étudiant public (sans compte) : inscription et réinscription en ligne
    path('etudiant/', views_espace.espace_accueil, name='espace_accueil'),
    path('etudiant/dossier/', views_espace.espace_dossier, name='espace_dossier'),
    path('etudiant/dossier/envoye/', views_espace.espace_confirmation, name='espace_confirmation'),
    path('etudiant/quitter/', views_espace.espace_quitter, name='espace_quitter'),
    path('connexion/', auth_views.LoginView.as_view(template_name='pfe/connexion.html',
                                                     redirect_authenticated_user=True), name='login'),
    path('deconnexion/', auth_views.LogoutView.as_view(), name='logout'),

    # Mot de passe oublié
    path('mot-de-passe-oublie/', auth_views.PasswordResetView.as_view(
        template_name='pfe/mdp_oublie.html',
        form_class=MotDePasseOublieForm,
        subject_template_name='pfe/emails/mdp_oublie_sujet.txt',
        email_template_name='pfe/emails/mdp_oublie.txt',
        html_email_template_name='pfe/emails/mdp_oublie.html',
    ), name='password_reset'),
    path('mot-de-passe-oublie/envoye/', auth_views.PasswordResetDoneView.as_view(
        template_name='pfe/mdp_oublie_envoye.html'), name='password_reset_done'),
    path('reinitialiser/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='pfe/mdp_nouveau.html', form_class=NouveauMotDePasseForm), name='password_reset_confirm'),
    path('reinitialiser/termine/', auth_views.PasswordResetCompleteView.as_view(
        template_name='pfe/mdp_termine.html'), name='password_reset_complete'),

    path('', include('pfe.urls')),
    # Photos des étudiants (media/) : réservées aux utilisateurs connectés, en développement comme en production
    re_path(r'^media/(?P<chemin>.+)$', media_protege, name='media'),
]
