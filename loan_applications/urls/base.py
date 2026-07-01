# loan_applications/urls/base.py
from django.urls import path

from loan_applications.views.loan_application import (
    LoanApplicationCRUDView,
    LoanApplicationApproveView,
    LoanApplicationRejectView,
    LoanApplicationStatisticsView,
    LoanApplicationRestoreView,
    LoanApplicationPermanentDeleteView,
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
    # Restore and Permanent Delete
    # ============================================================
    path(
        "loan-applications/<int:id>/restore/",
        LoanApplicationRestoreView.as_view(),
        name="loan-application-restore"
    ),
    path(
        "loan-applications/<int:id>/permanent/",
        LoanApplicationPermanentDeleteView.as_view(),
        name="loan-application-permanent-delete"
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