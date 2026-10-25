from django.contrib import admin

from .models import Licence, Personnel, Promotion, Semestre, Service, Session

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
    list_display = ('prenom', 'nom', 'fonc', 'email', 'utilisateur')
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


admin.site.register(Licence)
admin.site.register(Promotion)
