# urls/base.py
from django.urls import path

from borrowers.views.borrower import (
    BorrowerCRUDView,
    BorrowerRestoreView,
    BorrowerPermanentDeleteView,
    BorrowerBulkCreateView,
    BorrowerBulkUpdateView,
    BorrowerImportView,
    BorrowerExportView,
    BorrowerStatisticsView,
)
from borrowers.views.credit_check_log import (
    CreditCheckLogCRUDView,
    CreditCheckStatsView,
)

urlpatterns = [
    # ============================================================
    # Borrower CRUD
    # ============================================================
    path("", BorrowerCRUDView.as_view(), name="borrower-list-create"),
    path("<int:id>/", BorrowerCRUDView.as_view(), name="borrower-detail"),
    # ============================================================
    # Restore and Permanent Delete
    # ============================================================
    path("<int:id>/restore/", BorrowerRestoreView.as_view(), name="borrower-restore"),
    path(
        "<int:id>/permanent/",
        BorrowerPermanentDeleteView.as_view(),
        name="borrower-permanent-delete",
    ),
    # ============================================================
    # Bulk Operations
    # ============================================================
    path("bulkCreate/", BorrowerBulkCreateView.as_view(), name="borrower-bulk-create"),
    path("bulkUpdate/", BorrowerBulkUpdateView.as_view(), name="borrower-bulk-update"),
    path("import/", BorrowerImportView.as_view(), name="borrower-import"),
    path("export/", BorrowerExportView.as_view(), name="borrower-export"),
    path("statistics/", BorrowerStatisticsView.as_view(), name="borrower-statistics"),
    # ============================================================
    # Credit Check Log CRUD (existing)
    # ============================================================
    path(
        "credit-checks/statistics/",
        CreditCheckStatsView.as_view(),
        name="credit-check-stats",
    ),
    path(
        "credit-checks/",
        CreditCheckLogCRUDView.as_view(),
        name="credit-check-list-create",
    ),
    path(
        "credit-checks/<int:id>/",
        CreditCheckLogCRUDView.as_view(),
        name="credit-check-detail",
    )
]
