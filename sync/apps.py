# sync/apps.py
from django.apps import AppConfig


class SyncConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sync'
    verbose_name = 'Sync'

    def ready(self):
        """Initialize sync when app is ready."""
        # Auto-initialize sync metadata on startup (optional)
        # from sync.services.sync import SyncService
        # SyncService.initialize_entities()
        pass