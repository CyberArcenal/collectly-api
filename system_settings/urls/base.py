from django.urls import path

from system_settings.views.system_setting import (
    SystemSettingCRUDView,
    SystemSettingGroupedView,
    SystemSettingPublicView,
    SystemSettingSystemInfoView,
)


urlpatterns = [
    # ============================================================
    # System Setting CRUD
    # ============================================================
    path(
        "settings/",
        SystemSettingCRUDView.as_view(),
        name="setting-list-create"
    ),
    path(
        "settings/<int:id>/",
        SystemSettingCRUDView.as_view(),
        name="setting-detail"
    ),

    # ============================================================
    # System Setting Bulk Update
    # ============================================================
    path(
        "settings/bulk-update/",
        SystemSettingCRUDView.as_view(),
        name="setting-bulk-update"
    ),

    # ============================================================
    # System Setting Grouped
    # ============================================================
    path(
        "settings/grouped/",
        SystemSettingGroupedView.as_view(),
        name="setting-grouped"
    ),

    # ============================================================
    # System Setting Public
    # ============================================================
    path(
        "settings/public/",
        SystemSettingPublicView.as_view(),
        name="setting-public"
    ),

    # ============================================================
    # System Information
    # ============================================================
    path(
        "system-info/",
        SystemSettingSystemInfoView.as_view(),
        name="system-info"
    ),
]