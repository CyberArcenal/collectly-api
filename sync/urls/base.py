# sync/urls/base.py
from django.urls import path

from sync.views.sync import (
    SyncView,
    SyncStatusView,
    SyncEntityView,
    SyncConflictsView,
    SyncAutoResolveView,
    SyncQueueView,
    SyncProcessQueueView,
    SyncCleanupView,
    SyncResetView,
    SyncHealthView,
    SyncTestView,
)


urlpatterns = [
    # ============================================================
    # SPECIFIC PATHS (MUST COME BEFORE DYNAMIC)
    # ============================================================
    
    # Status & Health
    path("status/", SyncStatusView.as_view(), name="sync-status"),
    path("health/", SyncHealthView.as_view(), name="sync-health"),
    
    # Conflicts
    path("conflicts/", SyncConflictsView.as_view(), name="sync-conflicts"),
    path("conflicts/auto-resolve/", SyncAutoResolveView.as_view(), name="sync-auto-resolve"),
    
    # Queue
    path("queue/", SyncQueueView.as_view(), name="sync-queue"),
    path("queue/enqueue/", SyncQueueView.as_view(), name="sync-enqueue"),
    path("queue/process/", SyncProcessQueueView.as_view(), name="sync-process-queue"),
    
    # Maintenance
    path("cleanup/", SyncCleanupView.as_view(), name="sync-cleanup"),
    path("reset/", SyncResetView.as_view(), name="sync-reset"),
    path("test/", SyncTestView.as_view(), name="sync-test"),
    
    # ============================================================
    # DYNAMIC PATH (MUST COME LAST)
    # ============================================================
    
    # Receive sync data from client
    # POST /api/v1/sync/{entity_name}/
    path("<str:entity_name>/", SyncView.as_view(), name="sync-receive"),
    
    # Trigger sync for specific entity
    # POST /api/v1/sync/{entity_name}/trigger/
    path("<str:entity_name>/trigger/", SyncEntityView.as_view(), name="sync-entity"),
]