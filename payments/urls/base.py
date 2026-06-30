from django.urls import path

from payments.views.payment_transaction import (
    PaymentTransactionCRUDView,
    PaymentTransactionVoidView,
    PaymentTransactionStatisticsView,
    PaymentCollectionReportView,
)
from payments.views.penalty_transaction import (
    PenaltyTransactionCRUDView,
    PenaltyTransactionStatisticsView,
    PenaltyTransactionAutoRunView,
)


urlpatterns = [
    # ============================================================
    # Payment Transaction CRUD
    # ============================================================
    path(
        "payments/",
        PaymentTransactionCRUDView.as_view(),
        name="payment-list-create"
    ),
    path(
        "payments/<int:id>/",
        PaymentTransactionCRUDView.as_view(),
        name="payment-detail"
    ),

    # ============================================================
    # Payment Transaction Actions
    # ============================================================
    path(
        "payments/<int:id>/void/",
        PaymentTransactionVoidView.as_view(),
        name="payment-void"
    ),

    # ============================================================
    # Payment Transaction Reports
    # ============================================================
    path(
        "payments/stats/",
        PaymentTransactionStatisticsView.as_view(),
        name="payment-stats"
    ),
    path(
        "payments/collection-report/",
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
    # Penalty Transaction Statistics
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