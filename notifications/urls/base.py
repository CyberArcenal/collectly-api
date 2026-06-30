from django.urls import path

from notifications.views.notification import (
    NotificationCRUDView,
    NotificationMarkReadView,
    NotificationMarkAllReadView,
    NotificationUnreadCountView,
    NotificationStatisticsView,
)
from notifications.views.notification_log import (
    NotificationLogCRUDView,
    NotificationLogRetryView,
)


urlpatterns = [
    # ============================================================
    # Notification CRUD
    # ============================================================
    path(
        "notifications/",
        NotificationCRUDView.as_view(),
        name="notification-list-create"
    ),
    path(
        "notifications/<int:id>/",
        NotificationCRUDView.as_view(),
        name="notification-detail"
    ),

    # ============================================================
    # Notification Actions
    # ============================================================
    path(
        "notifications/<int:id>/mark-read/",
        NotificationMarkReadView.as_view(),
        name="notification-mark-read"
    ),
    path(
        "notifications/mark-all-read/",
        NotificationMarkAllReadView.as_view(),
        name="notification-mark-all-read"
    ),
    path(
        "notifications/unread-count/",
        NotificationUnreadCountView.as_view(),
        name="notification-unread-count"
    ),

    # ============================================================
    # Notification Statistics
    # ============================================================
    path(
        "notifications/stats/",
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
    # Notification Log Actions
    # ============================================================
    path(
        "notification-logs/<int:id>/retry/",
        NotificationLogRetryView.as_view(),
        name="notification-log-retry"
    ),
]