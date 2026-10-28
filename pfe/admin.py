from django.contrib import admin

from .models import (Licence, Personnel, Promotion, Promotion_Licence_Session, ReglageAffichage, Semestre, Service,
                     Session)

admin.site.site_header = "gePed · Paramétrage"
admin.site.site_title = "gePed"


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('nom', 'code', 'programme', 'departement')
    list_filter = ('programme', ('departement', admin.RelatedOnlyFieldListFilter))
    search_fields = ('nom', 'code')
    ordering = ('-programme', 'nom')
    autocomplete_fields = ('departement',)
    list_select_related = ('departement',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        champ = form.base_fields.get('departement')
        if champ:
            champ.help_text = ("Département du programme : un autre service, ou le programme lui-même s'il est aussi "
                               "département (ex. Génie Informatique). Vide : le programme sert de département.")
        return form


@admin.register(Personnel)
class PersonnelAdmin(admin.ModelAdmin):
    list_display = ('civilite', 'prenom', 'nom', 'fonc', 'chef', 'email', 'utilisateur')
    search_fields = ('prenom', 'nom', 'email')
    filter_horizontal = ('service',)
    autocomplete_fields = ('utilisateur',)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('annee', 'en_cours')


@admin.register(Semestre)
class SemestreAdmin(admin.ModelAdmin):
    list_display = ('nom', 'abrege', 'licence')
    list_filter = ('licence',)
    ordering = ('licence__nom', 'nom')
    search_fields = ('nom', 'abrege', 'licence__nom')


admin.site.register(Licence)


class NiveauPromotionInline(admin.TabularInline):
    """Niveau de la promotion pour chaque session (Promotion_Licence_Session) : sert aux inscriptions en ligne."""
    model = Promotion_Licence_Session
    extra = 1
    ordering = ('session__annee',)
    verbose_name = 'Niveau pour une session'
    verbose_name_plural = 'Niveaux par session (inscriptions et réinscriptions en ligne)'


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ('nom', 'description')
    search_fields = ('nom',)
    inlines = [NiveauPromotionInline]


@admin.register(Promotion_Licence_Session)
class PromotionLicenceSessionAdmin(admin.ModelAdmin):
    list_display = ('promotion', 'licence', 'session')
    list_filter = ('session', 'licence', 'promotion')
    search_fields = ('promotion__nom', 'licence__nom', 'session__annee')
    ordering = ('-session__annee', 'promotion__nom', 'licence__nom')
    list_select_related = ('promotion', 'licence', 'session')


@admin.register(ReglageAffichage)
class ReglageAffichageAdmin(admin.ModelAdmin):
    """Une seule ligne de réglages : on la modifie, on n'en ajoute ni n'en supprime."""
    list_display = ('__str__', 'nb_releves')

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not ReglageAffichage.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
