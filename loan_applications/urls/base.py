from django.urls import path

from loan_applications.views.loan_application import (
    LoanApplicationCRUDView,
    LoanApplicationApproveView,
    LoanApplicationRejectView,
    LoanApplicationStatisticsView,
)


urlpatterns = [
    # ============================================================
    # Loan Application CRUD
    # ============================================================
    path(
        "loan-applications/",
        LoanApplicationCRUDView.as_view(),
        name="loan-application-list-create"
    ),
    path(
        "loan-applications/<int:id>/",
        LoanApplicationCRUDView.as_view(),
        name="loan-application-detail"
    ),

    # ============================================================
    # Loan Application Actions
    # ============================================================
    path(
        "loan-applications/<int:id>/approve/",
        LoanApplicationApproveView.as_view(),
        name="loan-application-approve"
    ),
    path(
        "loan-applications/<int:id>/reject/",
        LoanApplicationRejectView.as_view(),
        name="loan-application-reject"
    ),

    # ============================================================
    # Loan Application Statistics
    # ============================================================
    path(
        "loan-applications/stats/",
        LoanApplicationStatisticsView.as_view(),
        name="loan-application-stats"
    ),
]