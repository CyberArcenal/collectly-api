from django.apps import AppConfig


class LoanApplicationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "loan_applications"
    
    def ready(self):
        import loan_applications.signals
        return super().ready()
