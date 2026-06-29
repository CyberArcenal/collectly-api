# audit/services/policy.py
import logging
from django.db import transaction
from django.shortcuts import get_object_or_404
from audit.models.policy import AuditPolicy

logger = logging.getLogger(__name__)


class AuditPolicyService:
    """
    Service layer for AuditPolicy operations.
    Handles policy creation, retrieval, and management.
    """

    # ======================================================================
    # GETTERS
    # ======================================================================

    @staticmethod
    def get_policy_by_id(policy_id: int) -> AuditPolicy:
        """
        Get an audit policy by its primary key.

        Args:
            policy_id: The policy ID.

        Returns:
            AuditPolicy: The policy instance.

        Raises:
            AuditPolicy.DoesNotExist: If policy not found.
        """
        return AuditPolicy.objects.get(id=policy_id)

    @staticmethod
    def get_active_policy() -> AuditPolicy:
        """
        Get the active audit policy (usually the first one).

        Returns:
            AuditPolicy: The active policy instance, or None if none exists.
        """
        return AuditPolicy.objects.first()

    @staticmethod
    def get_all_policies() -> list:
        """
        Get all audit policies.

        Returns:
            list: List of AuditPolicy objects.
        """
        return AuditPolicy.objects.all().order_by('created_at')

    # ======================================================================
    # CRUD OPERATIONS
    # ======================================================================

    @staticmethod
    @transaction.atomic
    def create_policy(validated_data: dict) -> AuditPolicy:
        """
        Create a new audit policy.

        Args:
            validated_data: Validated data from the serializer.

        Returns:
            AuditPolicy: The created policy instance.

        Note:
            If a policy already exists, creation may be restricted.
            The model allows only one policy, but we can handle multiple if needed.
        """
        # Ensure only one policy exists (optional)
        if AuditPolicy.objects.exists():
            logger.warning("A policy already exists. Only one policy is recommended.")
            # Allow creation but log warning

        policy = AuditPolicy.objects.create(**validated_data)

        logger.info(
            f"AuditPolicy created: id={policy.id}, "
            f"retention_years={policy.retention_years}, "
            f"immutable={policy.immutable}"
        )

        return policy

    @staticmethod
    @transaction.atomic
    def update_policy(instance: AuditPolicy, validated_data: dict) -> AuditPolicy:
        """
        Update an existing audit policy.

        Args:
            instance: The existing AuditPolicy instance.
            validated_data: Validated data from the serializer.

        Returns:
            AuditPolicy: The updated policy instance.

        Raises:
            ValueError: If the policy is immutable and cannot be updated.
        """
        if instance.immutable:
            raise ValueError("This policy is immutable and cannot be updated.")

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        logger.info(
            f"AuditPolicy {instance.id} updated: "
            f"retention_years={instance.retention_years}, "
            f"immutable={instance.immutable}"
        )

        return instance

    @staticmethod
    @transaction.atomic
    def delete_policy(policy_id: int) -> None:
        """
        Delete an audit policy.

        Args:
            policy_id: The policy ID to delete.

        Raises:
            ValueError: If the policy is immutable and cannot be deleted.
        """
        policy = get_object_or_404(AuditPolicy, id=policy_id)

        if policy.immutable:
            raise ValueError("Cannot delete an immutable policy.")

        policy.delete()

        logger.info(f"AuditPolicy {policy_id} deleted")

    @staticmethod
    @transaction.atomic
    def toggle_immutable(policy_id: int) -> AuditPolicy:
        """
        Toggle the immutable flag of a policy.

        Args:
            policy_id: The policy ID.

        Returns:
            AuditPolicy: The updated policy instance.

        Raises:
            ValueError: If the policy is already immutable.
        """
        policy = get_object_or_404(AuditPolicy, id=policy_id)

        if policy.immutable:
            raise ValueError("Cannot modify an immutable policy.")

        policy.immutable = not policy.immutable
        policy.save()

        status = "enabled" if policy.immutable else "disabled"
        logger.info(f"AuditPolicy {policy_id} immutability {status}")

        return policy

    # ======================================================================
    # STATISTICS
    # ======================================================================

    @staticmethod
    def get_policy_statistics() -> dict:
        """
        Get audit policy statistics.

        Returns:
            dict: Statistics about audit policies.
        """
        total_policies = AuditPolicy.objects.count()
        immutable_count = AuditPolicy.objects.filter(immutable=True).count()
        mutable_count = AuditPolicy.objects.filter(immutable=False).count()

        # Get retention years distribution
        retention_values = AuditPolicy.objects.values_list('retention_years', flat=True)

        return {
            "total_policies": total_policies,
            "immutable_count": immutable_count,
            "mutable_count": mutable_count,
            "retention_years": list(retention_values),
            "avg_retention": sum(retention_values) / len(retention_values) if retention_values else 0,
            "max_retention": max(retention_values) if retention_values else 0,
            "min_retention": min(retention_values) if retention_values else 0,
        }