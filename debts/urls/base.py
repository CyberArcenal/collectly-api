from django.urls import path

from debts.views.debt import (
    DebtCRUDView,
    DebtStatisticsView,
    DebtAgingSummaryView,
    DebtCollectionScheduleView,
)
from debts.views.forgiveness import ForgivenessLogCRUDView
from debts.views.interest_rate_change import InterestRateChangeLogCRUDView


urlpatterns = [
    # ============================================================
    # Debt CRUD
    # ============================================================
    path(
        "debts/",
        DebtCRUDView.as_view(),
        name="debt-list-create"
    ),
    path(
        "debts/<int:id>/",
        DebtCRUDView.as_view(),
        name="debt-detail"
    ),

    # ============================================================
    # Debt Statistics
    # ============================================================
    path(
        "debts/stats/",
        DebtStatisticsView.as_view(),
        name="debt-stats"
    ),

    # ============================================================
    # Debt Aging Summary
    # ============================================================
    path(
        "debts/aging-summary/",
        DebtAgingSummaryView.as_view(),
        name="debt-aging-summary"
    ),

    # ============================================================
    # Debt Collection Schedule
    # ============================================================
    path(
        "debts/collection-schedule/",
        DebtCollectionScheduleView.as_view(),
        name="debt-collection-schedule"
    ),

    # ============================================================
    # Forgiveness Log CRUD
    # ============================================================
    path(
        "forgiveness-logs/",
        ForgivenessLogCRUDView.as_view(),
        name="forgiveness-log-list-create"
    ),
    path(
        "forgiveness-logs/<int:id>/",
        ForgivenessLogCRUDView.as_view(),
        name="forgiveness-log-detail"
    ),

    # ============================================================
    # Interest Rate Change Log CRUD
    # ============================================================
    path(
        "interest-rate-changes/",
        InterestRateChangeLogCRUDView.as_view(),
        name="interest-rate-change-list-create"
    ),
    path(
        "interest-rate-changes/<int:id>/",
        InterestRateChangeLogCRUDView.as_view(),
        name="interest-rate-change-detail"
    ),
]