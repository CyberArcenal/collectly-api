# sync/admin.py
from django.contrib import admin
from sync.models.sync_metadata import SyncMetadata
from sync.models.sync_conflict import SyncConflict
from sync.models.sync_queue import SyncQueue


@admin.register(SyncMetadata)
class SyncMetadataAdmin(admin.ModelAdmin):
    list_display = ['entity', 'status', 'last_synced_at', 'total_synced', 'updated_at']
    list_filter = ['status']
    search_fields = ['entity']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['entity']


@admin.register(SyncConflict)
class SyncConflictAdmin(admin.ModelAdmin):
    list_display = ['entity', 'entity_id', 'resolution', 'resolved_by', 'created_at']
    list_filter = ['entity', 'resolution']
    search_fields = ['entity', 'entity_id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(SyncQueue)
class SyncQueueAdmin(admin.ModelAdmin):
    list_display = ['entity', 'entity_id', 'action', 'status', 'retry_count', 'created_at']
    list_filter = ['entity', 'status']
    search_fields = ['entity', 'entity_id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']