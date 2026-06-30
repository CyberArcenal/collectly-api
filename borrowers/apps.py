from django.apps import AppConfig


class BorrowersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "borrowers"
    
    def ready(self):
        import borrowers.signals
        return super().ready()
