# loan_agreements/urls/base.py
from django.urls import path

from loan_agreements.views.loan_agreement import (
    LoanAgreementCRUDView,
    LoanAgreementSignView,
    LoanAgreementRestoreView,
    LoanAgreementPermanentDeleteView,
    LoanAgreementBulkCreateView,
    LoanAgreementBulkUpdateView,
    LoanAgreementImportView,
    LoanAgreementExportView,
    LoanAgreementStatisticsView,
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
    # Restore and Permanent Delete
    # ============================================================
    path(
        "loan-agreements/<int:id>/restore/",
        LoanAgreementRestoreView.as_view(),
        name="loan-agreement-restore"
    ),
    path(
        "loan-agreements/<int:id>/permanent/",
        LoanAgreementPermanentDeleteView.as_view(),
        name="loan-agreement-permanent-delete"
    ),

    # ============================================================
    # Bulk Operations
    # ============================================================
    path(
        "loan-agreements/bulkCreate/",
        LoanAgreementBulkCreateView.as_view(),
        name="loan-agreement-bulk-create"
    ),
    path(
        "loan-agreements/bulkUpdate/",
        LoanAgreementBulkUpdateView.as_view(),
        name="loan-agreement-bulk-update"
    ),
    path(
        "loan-agreements/import/",
        LoanAgreementImportView.as_view(),
        name="loan-agreement-import"
    ),
    path(
        "loan-agreements/export/",
        LoanAgreementExportView.as_view(),
        name="loan-agreement-export"
    ),

    # ============================================================
    # Statistics
    # ============================================================
    path(
        "loan-agreements/statistics/",
        LoanAgreementStatisticsView.as_view(),
        name="loan-agreement-statistics"
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