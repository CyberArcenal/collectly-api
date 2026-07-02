# urls/base.py
from django.urls import path

from payments.views.payment_transaction import (
    PaymentTransactionCRUDView,
    PaymentTransactionVoidView,
    PaymentTransactionStatisticsView,
    PaymentCollectionReportView,
    PaymentRestoreView,
    PaymentPermanentDeleteView,
    PaymentBulkCreateView,
    PaymentBulkUpdateView,
    PaymentImportView,
    PaymentExportView,
)
from payments.views.penalty_transaction import (
    PenaltyTransactionCRUDView,
    PenaltyTransactionStatisticsView,
    PenaltyTransactionAutoRunView,
    PenaltyRestoreView,
    PenaltyPermanentDeleteView,
    PenaltyBulkCreateView,
    PenaltyBulkUpdateView,
    PenaltyImportView,
    PenaltyExportView,
    PenaltyTotalByDebtView,
)


urlpatterns = [
    # ============================================================
    # Payment Transaction CRUD
    # ============================================================
    path(
        "",
        PaymentTransactionCRUDView.as_view(),
        name="payment-list-create"
    ),
    path(
        "<int:id>/",
        PaymentTransactionCRUDView.as_view(),
        name="payment-detail"
    ),

    # ============================================================
    # Payment Restore & Permanent Delete
    # ============================================================
    path(
        "<int:id>/restore/",
        PaymentRestoreView.as_view(),
        name="payment-restore"
    ),
    path(
        "<int:id>/permanent/",
        PaymentPermanentDeleteView.as_view(),
        name="payment-permanent-delete"
    ),

    # ============================================================
    # Payment Bulk Operations
    # ============================================================
    path(
        "bulkCreate/",
        PaymentBulkCreateView.as_view(),
        name="payment-bulk-create"
    ),
    path(
        "bulkUpdate/",
        PaymentBulkUpdateView.as_view(),
        name="payment-bulk-update"
    ),
    path(
        "import/",
        PaymentImportView.as_view(),
        name="payment-import"
    ),
    path(
        "export/",
        PaymentExportView.as_view(),
        name="payment-export"
    ),

    # ============================================================
    # Payment Transaction Actions
    # ============================================================
    path(
        "<int:id>/void/",
        PaymentTransactionVoidView.as_view(),
        name="payment-void"
    ),

    # ============================================================
    # Payment Transaction Reports
    # ============================================================
    path(
        "stats/",
        PaymentTransactionStatisticsView.as_view(),
        name="payment-stats"
    ),
    path(
        "collection-report/",
        PaymentCollectionReportView.as_view(),
        name="payment-collection-report"
    ),

    # ============================================================
    # Penalty Transaction CRUD
    # ============================================================
    path(
        "penalties/",
        PenaltyTransactionCRUDView.as_view(),
        name="penalty-list-create"
    ),
    path(
        "penalties/<int:id>/",
        PenaltyTransactionCRUDView.as_view(),
        name="penalty-detail"
    ),

    # ============================================================
    # Penalty Restore & Permanent Delete
    # ============================================================
    path(
        "penalties/<int:id>/restore/",
        PenaltyRestoreView.as_view(),
        name="penalty-restore"
    ),
    path(
        "penalties/<int:id>/permanent/",
        PenaltyPermanentDeleteView.as_view(),
        name="penalty-permanent-delete"
    ),

    # ============================================================
    # Penalty Bulk Operations
    # ============================================================
    path(
        "penalties/bulkCreate/",
        PenaltyBulkCreateView.as_view(),
        name="penalty-bulk-create"
    ),
    path(
        "penalties/bulkUpdate/",
        PenaltyBulkUpdateView.as_view(),
        name="penalty-bulk-update"
    ),
    path(
        "penalties/import/",
        PenaltyImportView.as_view(),
        name="penalty-import"
    ),
    path(
        "penalties/export/",
        PenaltyExportView.as_view(),
        name="penalty-export"
    ),

    # ============================================================
    # Penalty Total By Debt
    # ============================================================
    path(
        "penalties/total-by-debt/",
        PenaltyTotalByDebtView.as_view(),
        name="penalty-total-by-debt"
    ),

    # ============================================================
    # Penalty Statistics
    # ============================================================
    path(
        "penalties/stats/",
        PenaltyTransactionStatisticsView.as_view(),
        name="penalty-stats"
    ),

    # ============================================================
    # Penalty Auto Run
    # ============================================================
    path(
        "penalties/auto-run/",
        PenaltyTransactionAutoRunView.as_view(),
        name="penalty-auto-run"
    ),
]