# urls/base.py
from django.urls import path

from groups.views.group import (
    GroupCRUDView,
    GroupMemberCRUDView,
    GroupStatsView,
    GroupsForDebtorView,
    GroupBulkAssignView,
    GroupClearMembersView,
    GroupRemoveMemberView,
)


urlpatterns = [
    # ============================================================
    # Group CRUD
    # ============================================================
    path(
        "",
        GroupCRUDView.as_view(),
        name="group-list-create"
    ),
    path(
        "<int:id>/",
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
    # Groups by Debtor
    # ============================================================
    path(
        "by-debtor/<int:debtor_id>/",
        GroupsForDebtorView.as_view(),
        name="groups-by-debtor"
    ),

    # ============================================================
    # Group Bulk Assign
    # ============================================================
    path(
        "<int:group_id>/bulk-assign/",
        GroupBulkAssignView.as_view(),
        name="group-bulk-assign"
    ),

    # ============================================================
    # Group Clear Members
    # ============================================================
    path(
        "<int:group_id>/clear-members/",
        GroupClearMembersView.as_view(),
        name="group-clear-members"
    ),

    # ============================================================
    # Group Remove Member (alternative RESTful path)
    # ============================================================
    path(
        "<int:group_id>/members/<int:debtor_id>/",
        GroupRemoveMemberView.as_view(),
        name="group-remove-member"
    ),

    # ============================================================
    # Group Statistics
    # ============================================================
    path(
        "stats/",
        GroupStatsView.as_view(),
        name="group-stats"
    ),
]