# urls/base.py
from django.urls import path

from notifications.views.notification import (
    NotificationCRUDView,
    NotificationMarkReadView,
    NotificationMarkAllReadView,
    NotificationUnreadCountView,
    NotificationStatisticsView,
    NotificationRestoreView,
    NotificationPermanentDeleteView,
    NotificationMarkManyReadView,
    NotificationBulkCreateView,
    NotificationBulkUpdateView,
    NotificationImportView,
    NotificationExportView,
)
from notifications.views.notification_log import (
    NotificationLogCRUDView,
    NotificationLogRetryView,
    NotificationLogByRecipientView,
    NotificationLogSearchView,
    NotificationLogResendView,
    NotificationLogRetryAllView,
    NotificationLogStatsView,
)


urlpatterns = [
    # ============================================================
    # Notification CRUD
    # ============================================================
    path(
        "",
        NotificationCRUDView.as_view(),
        name="notification-list-create"
    ),
    path(
        "<int:id>/",
        NotificationCRUDView.as_view(),
        name="notification-detail"
    ),

    # ============================================================
    # Restore and Permanent Delete
    # ============================================================
    path(
        "<int:id>/restore/",
        NotificationRestoreView.as_view(),
        name="notification-restore"
    ),
    path(
        "<int:id>/permanent/",
        NotificationPermanentDeleteView.as_view(),
        name="notification-permanent-delete"
    ),

    # ============================================================
    # Bulk Operations
    # ============================================================
    path(
        "bulkCreate/",
        NotificationBulkCreateView.as_view(),
        name="notification-bulk-create"
    ),
    path(
        "bulkUpdate/",
        NotificationBulkUpdateView.as_view(),
        name="notification-bulk-update"
    ),
    path(
        "import/",
        NotificationImportView.as_view(),
        name="notification-import"
    ),
    path(
        "export/",
        NotificationExportView.as_view(),
        name="notification-export"
    ),

    # ============================================================
    # Notification Actions
    # ============================================================
    path(
        "<int:id>/mark-read/",
        NotificationMarkReadView.as_view(),
        name="notification-mark-read"
    ),
    path(
        "mark-many-read/",
        NotificationMarkManyReadView.as_view(),
        name="notification-mark-many-read"
    ),
    path(
        "mark-all-read/",
        NotificationMarkAllReadView.as_view(),
        name="notification-mark-all-read"
    ),
    path(
        "unread-count/",
        NotificationUnreadCountView.as_view(),
        name="notification-unread-count"
    ),

    # ============================================================
    # Notification Statistics
    # ============================================================
    path(
        "stats/",
        NotificationStatisticsView.as_view(),
        name="notification-stats"
    ),

    # ============================================================
    # Notification Log CRUD
    # ============================================================
    path(
        "notification-logs/",
        NotificationLogCRUDView.as_view(),
        name="notification-log-list-create"
    ),
    path(
        "notification-logs/<int:id>/",
        NotificationLogCRUDView.as_view(),
        name="notification-log-detail"
    ),

    # ============================================================
    # Notification Log by Recipient
    # ============================================================
    path(
        "notification-logs/by-recipient/",
        NotificationLogByRecipientView.as_view(),
        name="notification-log-by-recipient"
    ),

    # ============================================================
    # Notification Log Search
    # ============================================================
    path(
        "notification-logs/search/",
        NotificationLogSearchView.as_view(),
        name="notification-log-search"
    ),

    # ============================================================
    # Notification Log Actions
    # ============================================================
    path(
        "notification-logs/<int:id>/retry/",
        NotificationLogRetryView.as_view(),
        name="notification-log-retry"
    ),
    path(
        "notification-logs/<int:id>/resend/",
        NotificationLogResendView.as_view(),
        name="notification-log-resend"
    ),
    path(
        "notification-logs/retry-all/",
        NotificationLogRetryAllView.as_view(),
        name="notification-log-retry-all"
    ),
    path(
        "notification-logs/stats/",
        NotificationLogStatsView.as_view(),
        name="notification-log-stats"
    ),
]