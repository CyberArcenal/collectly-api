# sync/urls.py
from django.urls import path

from sync.views.sync import (
    SyncAutoResolveView,
    SyncCleanupView,
    SyncConflictsView,
    SyncEntityView,
    SyncHealthView,
    SyncProcessQueueView,
    SyncQueueView,
    SyncResetView,
    SyncStatusView,
    SyncTaskListView,
    SyncTaskStatusView,
    SyncTestView,
    SyncView,
)

urlpatterns = [
    # Task management (NEW)
    path("task/<str:task_id>/", SyncTaskStatusView.as_view(), name="sync-task-status"),
    path("tasks/", SyncTaskListView.as_view(), name="sync-task-list"),
    # Status & monitoring
    path("status/", SyncStatusView.as_view(), name="sync-status"),
    path("health/", SyncHealthView.as_view(), name="sync-health"),
    # Conflict management
    path("conflicts/", SyncConflictsView.as_view(), name="sync-conflicts"),
    path(
        "conflicts/<int:conflict_id>/resolve/",
        SyncConflictsView.as_view(),
        name="sync-conflict-resolve",
    ),
    path(
        "conflicts/auto-resolve/",
        SyncAutoResolveView.as_view(),
        name="sync-auto-resolve",
    ),
    # Queue management
    path("queue/", SyncQueueView.as_view(), name="sync-queue"),
    path("queue/process/", SyncProcessQueueView.as_view(), name="sync-process-queue"),
    # Maintenance
    path("cleanup/", SyncCleanupView.as_view(), name="sync-cleanup"),
    path("reset/", SyncResetView.as_view(), name="sync-reset"),
    # Debug
    path("test/", SyncTestView.as_view(), name="sync-test"),
    # Main sync endpoints
    path("<str:entity_name>/", SyncView.as_view(), name="sync-pull"),
    path("<str:entity_name>/trigger/", SyncEntityView.as_view(), name="sync-trigger"),
]
