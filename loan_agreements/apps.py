from django.apps import AppConfig


class LoanAgreementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "loan_agreements"
    
    def ready(self):
        import loan_agreements.signals
        return super().ready()
