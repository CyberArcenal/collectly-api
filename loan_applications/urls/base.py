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
        "",
        LoanApplicationCRUDView.as_view(),
        name="loan-application-list-create"
    ),
    path(
        "<int:id>/",
        LoanApplicationCRUDView.as_view(),
        name="loan-application-detail"
    ),

    # ============================================================
    # Restore and Permanent Delete
    # ============================================================
    path(
        "<int:id>/restore/",
        LoanApplicationRestoreView.as_view(),
        name="loan-application-restore"
    ),
    path(
        "<int:id>/permanent/",
        LoanApplicationPermanentDeleteView.as_view(),
        name="loan-application-permanent-delete"
    ),

    # ============================================================
    # Loan Application Actions
    # ============================================================
    path(
        "<int:id>/approve/",
        LoanApplicationApproveView.as_view(),
        name="loan-application-approve"
    ),
    path(
        "<int:id>/reject/",
        LoanApplicationRejectView.as_view(),
        name="loan-application-reject"
    ),

    # ============================================================
    # Loan Application Statistics
    # ============================================================
    path(
        "stats/",
        LoanApplicationStatisticsView.as_view(),
        name="loan-application-stats"
    ),
]