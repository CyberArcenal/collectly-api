# audit/urls/log.py
from django.urls import path
from audit.views.log import (
    AuditLogCRUD,
    AuditLogStatsView,
    AuditLogByEntityView,
    AuditLogByUserView,
    AuditLogByActionView,
    AuditLogByDateRangeView,
    AuditLogSearchView,
    AuditLogSummaryView,
    AuditLogCountsView,
    AuditLogTopActivitiesView,
    AuditLogRecentActivityView,
    AuditLogExportView,
    AuditLogGenerateReportView,
)


urlpatterns = [
    # ============================================================
    # Audit Log CRUD (list, retrieve, create, delete)
    # ============================================================
    path("logs/", AuditLogCRUD.as_view(), name="audit-log-list-create"),
    path("logs/<int:id>/", AuditLogCRUD.as_view(), name="audit-log-detail"),

    # ============================================================
    # Filtered Lists
    # ============================================================
    path("entity/", AuditLogByEntityView.as_view(), name="audit-log-by-entity"),
    path("user/", AuditLogByUserView.as_view(), name="audit-log-by-user"),
    path("action/", AuditLogByActionView.as_view(), name="audit-log-by-action"),
    path("date-range/", AuditLogByDateRangeView.as_view(), name="audit-log-by-date-range"),
    path("search/", AuditLogSearchView.as_view(), name="audit-log-search"),

    # ============================================================
    # Aggregations
    # ============================================================
    path("summary/", AuditLogSummaryView.as_view(), name="audit-log-summary"),
    path("stats/", AuditLogStatsView.as_view(), name="audit-log-stats"),
    path("counts/", AuditLogCountsView.as_view(), name="audit-log-counts"),
    path("top-activities/", AuditLogTopActivitiesView.as_view(), name="audit-log-top-activities"),
    path("recent/", AuditLogRecentActivityView.as_view(), name="audit-log-recent"),

    # ============================================================
    # Export / Report
    # ============================================================
    path("export/", AuditLogExportView.as_view(), name="audit-log-export"),
    path("report/", AuditLogGenerateReportView.as_view(), name="audit-log-report"),
]