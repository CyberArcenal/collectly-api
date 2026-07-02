from django.urls import path

from payment_methods.views.payment_method import (
    PaymentMethodCRUDView,
    PaymentMethodSetDefaultView,
    PaymentMethodStatsView,
    PaymentMethodAllStatsView,
)


urlpatterns = [
    # ============================================================
    # Payment Method CRUD
    # ============================================================
    path(
        "",
        PaymentMethodCRUDView.as_view(),
        name="payment-method-list-create"
    ),
    path(
        "<int:id>/",
        PaymentMethodCRUDView.as_view(),
        name="payment-method-detail"
    ),

    # ============================================================
    # Payment Method Actions
    # ============================================================
    path(
        "<int:id>/set-default/",
        PaymentMethodSetDefaultView.as_view(),
        name="payment-method-set-default"
    ),

    # ============================================================
    # Payment Method Statistics
    # ============================================================
    path(
        "<int:id>/stats/",
        PaymentMethodStatsView.as_view(),
        name="payment-method-stats"
    ),
    path(
        "stats/all/",
        PaymentMethodAllStatsView.as_view(),
        name="payment-method-all-stats"
    ),
]