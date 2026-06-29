import logging
from users.models.organization import Organization
logger = logging.getLogger(__name__)

class UserService:
    """
    Service layer for user app.
    Provides abstraction for Organization and UserProfile operations.
    """

    # -------------------------------
    # Organization methods
    # -------------------------------
    @staticmethod
    def get_organization_by_id(org_id):
        try:
            return Organization.objects.filter(id=org_id).first()
        except Exception as exc:
            logger.error(f"Error retrieving Organization {org_id}: {exc}")
            return None

    @staticmethod
    def search_organizations():
        return Organization.objects.all()

    @staticmethod
    def create_organization(**kwargs):
        try:
            org_obj = Organization.objects.create(**kwargs)
            return org_obj
        except Exception as exc:
            logger.error(f"Error creating Organization: {exc}")
            raise

    @staticmethod
    def update_organization(org_obj, user, **kwargs):
        try:
            for field, value in kwargs.items():
                setattr(org_obj, field, value)
            org_obj.save(update_fields=list(kwargs.keys()))
            return org_obj
        except Exception as exc:
            logger.error(f"Error updating Organization {org_obj.id}: {exc}")
            raise

    @staticmethod
    def delete_organization(org_obj, user):
        try:
            org_obj.delete()
        except Exception as exc:
            logger.error(f"Error deleting Organization {org_obj.id}: {exc}")
            raise