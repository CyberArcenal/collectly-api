# analytics/urls/base.py
from django.urls import path

from analytics.views.dashboard import (
    DashboardOverviewView,
    DashboardRevenueView,
    DashboardStatisticsView,
    DashboardTopProductsView,
    DashboardLowStockView,
    DashboardRecentActivitiesView,
    DashboardSalesTrendView,
    DashboardPaymentMethodsView,
)


urlpatterns = [
    # ============================================================
    # Dashboard Endpoints
    # ============================================================
    path(
        "dashboard/overview/",
        DashboardOverviewView.as_view(),
        name="dashboard-overview"
    ),
    path(
        "dashboard/revenue/",
        DashboardRevenueView.as_view(),
        name="dashboard-revenue"
    ),
    path(
        "dashboard/statistics/",
        DashboardStatisticsView.as_view(),
        name="dashboard-statistics"
    ),
    path(
        "dashboard/top-products/",
        DashboardTopProductsView.as_view(),
        name="dashboard-top-products"
    ),
    path(
        "dashboard/low-stock/",
        DashboardLowStockView.as_view(),
        name="dashboard-low-stock"
    ),
    path(
        "dashboard/recent-activities/",
        DashboardRecentActivitiesView.as_view(),
        name="dashboard-recent-activities"
    ),
    path(
        "dashboard/sales-trend/",
        DashboardSalesTrendView.as_view(),
        name="dashboard-sales-trend"
    ),
    path(
        "dashboard/payment-methods/",
        DashboardPaymentMethodsView.as_view(),
        name="dashboard-payment-methods"
    ),
]