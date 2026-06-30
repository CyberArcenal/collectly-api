from django.urls import path

from groups.views.group import (
    GroupCRUDView,
    GroupMemberCRUDView,
    GroupStatsView,
)


urlpatterns = [
    # ============================================================
    # Group CRUD
    # ============================================================
    path(
        "groups/",
        GroupCRUDView.as_view(),
        name="group-list-create"
    ),
    path(
        "groups/<int:id>/",
        GroupCRUDView.as_view(),
        name="group-detail"
    ),

    # ============================================================
    # Group Members CRUD
    # ============================================================
    path(
        "group-members/",
        GroupMemberCRUDView.as_view(),
        name="group-member-list-create-delete"
    ),

    # ============================================================
    # Group Statistics
    # ============================================================
    path(
        "groups/stats/",
        GroupStatsView.as_view(),
        name="group-stats"
    ),
]