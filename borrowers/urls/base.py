from django.urls import path

from borrowers.views.borrower import BorrowerCRUDView
from borrowers.views.credit_check_log import (
    CreditCheckLogCRUDView,
    CreditCheckStatsView,
)


urlpatterns = [
    # ============================================================
    # Borrower CRUD
    # ============================================================
    path(
        "borrowers/",
        BorrowerCRUDView.as_view(),
        name="borrower-list-create"
    ),
    path(
        "borrowers/<int:id>/",
        BorrowerCRUDView.as_view(),
        name="borrower-detail"
    ),

    # ============================================================
    # Credit Check Log CRUD
    # ============================================================
    path(
        "credit-checks/",
        CreditCheckLogCRUDView.as_view(),
        name="credit-check-list-create"
    ),
    path(
        "credit-checks/<int:id>/",
        CreditCheckLogCRUDView.as_view(),
        name="credit-check-detail"
    ),

    # ============================================================
    # Credit Check Statistics
    # ============================================================
    path(
        "credit-checks/stats/",
        CreditCheckStatsView.as_view(),
        name="credit-check-stats"
    ),
]