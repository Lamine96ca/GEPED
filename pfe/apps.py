from django.apps import AppConfig


class PfeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pfe'

    def ready(self):
        from . import signals  # noqa: F401
