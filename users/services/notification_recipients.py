from users.enums.base import UserRole
from users.models import User
import logging

logger = logging.getLogger(__name__)


def get_admin_and_staff_users():
    """
    Get all active admin and staff users for notifications.

    Returns:
        QuerySet: Users with role ADMIN or STAFF who are active
    """
    return User.objects.filter(
        user_type__in=[UserRole.ADMIN, UserRole.STAFF],
        is_deleted=False,
        status='active'  # Only active accounts
    )


def get_admin_users():
    """
    Get all active admin users.

    Returns:
        QuerySet: Users with role ADMIN who are active
    """
    return User.objects.filter(
        user_type=UserRole.ADMIN,
        is_deleted=False,
        status='active'
    )


def get_staff_users():
    """
    Get all active staff users.

    Returns:
        QuerySet: Users with role STAFF who are active
    """
    return User.objects.filter(
        user_type=UserRole.STAFF,
        is_deleted=False,
        status='active'
    )


def get_managers():
    """
    Get all active manager users.

    Returns:
        QuerySet: Users with role MANAGER who are active
    """
    return User.objects.filter(
        user_type=UserRole.MANAGER,
        is_deleted=False,
        status='active'
    )


def get_admin_staff_manager_users():
    """
    Get all active admin, staff, and manager users.

    Returns:
        QuerySet: Users with role ADMIN, STAFF, or MANAGER who are active
    """
    return User.objects.filter(
        user_type__in=[UserRole.ADMIN, UserRole.STAFF, UserRole.MANAGER],
        is_deleted=False,
        status='active'
    )