# urls/base.py
from django.urls import path

from debts.views.debt import (
    DebtCRUDView,
    DebtOverdueListView,
    DebtStatisticsView,
    DebtAgingSummaryView,
    DebtCollectionScheduleView,
    DebtRestoreView,
    DebtPermanentDeleteView,
    DebtBulkCreateView,
    DebtBulkUpdateView,
    DebtCorrectTotalAmountView,
    DebtRecalculateRemainingView,
    DebtApplyForgivenessView,
    DebtDebtsInBucketView,
    DebtMarkPeriodPaidView,
    DebtFixPrecisionView,
    DebtImportView,
    DebtExportView,
)
from debts.views.forgiveness import ForgivenessLogCRUDView
from debts.views.interest_rate_change import (
    InterestRateChangeLogCRUDView,
    InterestRateChangeLogStatisticsView,
)

urlpatterns = [
    # ============================================================
    # Debt CRUD
    # ============================================================
    path("", DebtCRUDView.as_view(), name="debt-list-create"),
    path("<int:id>/", DebtCRUDView.as_view(), name="debt-detail"),
    path("overdue/", DebtOverdueListView.as_view(), name="debt-overdue"),
    # ============================================================
    # Restore and Permanent Delete
    # ============================================================
    path("<int:id>/restore/", DebtRestoreView.as_view(), name="debt-restore"),
    path(
        "<int:id>/permanent/",
        DebtPermanentDeleteView.as_view(),
        name="debt-permanent-delete",
    ),
    # ============================================================
    # Bulk Operations
    # ============================================================
    path("bulkCreate/", DebtBulkCreateView.as_view(), name="debt-bulk-create"),
    path("bulkUpdate/", DebtBulkUpdateView.as_view(), name="debt-bulk-update"),
    path("import/", DebtImportView.as_view(), name="debt-import"),
    path("export/", DebtExportView.as_view(), name="debt-export"),
    # ============================================================
    # Debt Operations
    # ============================================================
    path(
        "<int:id>/correct-total/",
        DebtCorrectTotalAmountView.as_view(),
        name="debt-correct-total",
    ),
    path(
        "<int:id>/recalculate/",
        DebtRecalculateRemainingView.as_view(),
        name="debt-recalculate",
    ),
    path("<int:id>/forgive/", DebtApplyForgivenessView.as_view(), name="debt-forgive"),
    path(
        "mark-period-paid/",
        DebtMarkPeriodPaidView.as_view(),
        name="debt-mark-period-paid",
    ),
    path("fix-precision/", DebtFixPrecisionView.as_view(), name="debt-fix-precision"),
    # ============================================================
    # Debt Statistics & Reports
    # ============================================================
    path("stats/", DebtStatisticsView.as_view(), name="debt-stats"),
    path("aging-summary/", DebtAgingSummaryView.as_view(), name="debt-aging-summary"),
    path(
        "collection-schedule/",
        DebtCollectionScheduleView.as_view(),
        name="debt-collection-schedule",
    ),
    path("bucket/", DebtDebtsInBucketView.as_view(), name="debt-bucket"),
    # ============================================================
    # Forgiveness Log CRUD
    # ============================================================
    path(
        "forgiveness-logs/",
        ForgivenessLogCRUDView.as_view(),
        name="forgiveness-log-list-create",
    ),
    path(
        "forgiveness-logs/<int:id>/",
        ForgivenessLogCRUDView.as_view(),
        name="forgiveness-log-detail",
    ),
    # ============================================================
    # Interest Rate Change Log CRUD
    # ============================================================
    path(
        "interest-rate-changes/",
        InterestRateChangeLogCRUDView.as_view(),
        name="interest-rate-change-list-create",
    ),
    path(
        "interest-rate-changes/<int:id>/",
        InterestRateChangeLogCRUDView.as_view(),
        name="interest-rate-change-detail",
    ),
    path(
        "interest-rate-changes/stats/",
        InterestRateChangeLogStatisticsView.as_view(),
        name="interest-rate-change-stats",
    ),
]
