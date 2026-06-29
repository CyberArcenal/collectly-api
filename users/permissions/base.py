from rest_framework.permissions import BasePermission
from rest_framework import permissions
from audit.utils.log import log_audit_event
from users.enums.base import UserRole
from utils.security import get_client_ip
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
USER = get_user_model()

class BaseUserTypePermission(permissions.BasePermission):
    """Base class for user type permissions"""
    message = _("You do not have permission to perform this action.")
    ALLOWED_TYPES = []

    def has_permission(self, request, view):
        user = request.user
        allowed = bool(
            user.is_authenticated and getattr(user, "user_type", None) in self.ALLOWED_TYPES
        )
        if not allowed and user.is_authenticated:
            log_audit_event(
                request=request,
                user=user,
                action_type="permission_denied",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": f"{self.__class__.__name__} required"},
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        return allowed
    
class IsAccountActive(BasePermission):
    """
    Custom permission: only allow users with active accounts.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_active)
    

class IsAdmin(BaseUserTypePermission):
    ALLOWED_TYPES = ["admin"]
    
    
class IsStaff(BaseUserTypePermission):
    ALLOWED_TYPES = ["staff"]

class IsAccountActive(permissions.BasePermission):
    """Allows access only to users whose status is active"""
    message = _("Your account is not active.")

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user.is_authenticated and getattr(user, "status", None) == 'active'
        )





def is_admin(user) -> bool:
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN]

def is_staff(user) -> bool:
    """Helper function to check if user has edit permissions"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.STAFF, UserRole.MANAGER]

def is_collector(user) -> bool:
    """Check if user can manage collections."""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.COLLECTOR, UserRole.MANAGER, UserRole.ADMIN]

def can_edit(user) -> bool:
    """Helper function to check if user has edit permissions"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.STAFF, UserRole.MANAGER]

def can_approve(user) -> bool:
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.MANAGER]

def can_create(user) -> bool:
    """Helper function to check if user has edit permissions"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.STAFF, UserRole.MANAGER]

def can_read(user) -> bool:
    """Helper function to check if user has read permissions"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.STAFF, UserRole.MANAGER, UserRole.VIEWER]

def can_delete(user) -> bool:
    """Helper function to check if user has delete permissions"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN]

def can_confirm(user) -> bool:
    """Helper function to check if user can confirm purchases/orders"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.MANAGER]

def can_receive(user) -> bool:
    """Helper function to check if user can mark purchases/orders as received"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.STAFF]

def can_cancel(user) -> bool:
    """Helper function to check if user can cancel purchases/orders"""
    if not isinstance(user, USER):
        return False
    return user.user_type in [UserRole.ADMIN, UserRole.MANAGER]