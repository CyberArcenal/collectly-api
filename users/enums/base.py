# users/enums.py
from enum import Enum

class UserRole:
    VIEWER = "viewer"
    CUSTOMER = "customer"
    STAFF = "staff"
    COLLECTOR = "collector" 
    MANAGER = "manager"
    ADMIN = "admin"


class UserStatus:
    ACTIVE = "active"
    RESTRICTED = "restricted"
    SUSPENDED = "suspended"
    DELETED = "deleted"