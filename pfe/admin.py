from django.contrib import admin

from .models import Licence, Personnel, Promotion, Semestre, Service, Session

admin.site.site_header = "gePed · Paramétrage"
admin.site.site_title = "gePed"


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('nom', 'code', 'programme')
    list_filter = ('programme',)
    search_fields = ('nom', 'code')


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
