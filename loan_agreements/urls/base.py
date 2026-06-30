from django.urls import path

from loan_agreements.views.loan_agreement import (
    LoanAgreementCRUDView,
    LoanAgreementSignView,
)


urlpatterns = [
    # ============================================================
    # Loan Agreement CRUD
    # ============================================================
    path(
        "loan-agreements/",
        LoanAgreementCRUDView.as_view(),
        name="loan-agreement-list-create"
    ),
    path(
        "loan-agreements/<int:id>/",
        LoanAgreementCRUDView.as_view(),
        name="loan-agreement-detail"
    ),

    # ============================================================
    # Loan Agreement Sign
    # ============================================================
    path(
        "loan-agreements/<int:id>/sign/",
        LoanAgreementSignView.as_view(),
        name="loan-agreement-sign"
    ),
]