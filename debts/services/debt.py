from datetime import datetime
import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Max, Sum, Count, OuterRef, Subquery
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event

from debts.models.debt import Debt
from borrowers.models.borrower import Borrower
from payments.models.payment_transaction import PaymentTransaction
from payments.models.penalty_transaction import PenaltyTransaction
from system_settings.utils import (
    default_interest_rate,
    default_penalty_rate,
    default_interest_calculation_period,
)
from utils.helpers import camel_to_snake
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class DebtService:
    """
    Service layer for Debt CRUD operations.
    Handles business logic for debt management including creation, updates,
    statistics, and aging analysis.
    """

    # ============================================================
    # READ OPERATIONS
    # ============================================================

    @staticmethod
    def get_by_id(debt_id, include_deleted=False):
        """
        Get a single debt by ID.

        Args:
            debt_id: The ID of the debt to retrieve
            include_deleted: Whether to include soft-deleted debts

        Returns:
            Debt instance or None if not found
        """
        qs = Debt.objects.select_related("borrower")
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        try:
            return qs.get(id=debt_id)
        except Debt.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by="due_date", sort_order="asc"):
        """
        Get paginated list of debts with filters.

        Args:
            filters: Dictionary of filter criteria
            page: Page number for pagination
            limit: Number of items per page
            sort_by: Field to sort by
            sort_order: 'asc' or 'desc'

        Returns:
            dict: {
                'data': list of Debt objects,
                'pagination': pagination metadata
            }
        """
        qs = Debt.objects.select_related("borrower")

        # Handle deleted filtering based on include_deleted flag
        include_deleted = filters.get("include_deleted", False) if filters else False
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        # Apply filters
        if filters:
            if filters.get("search"):
                search = filters["search"]
                qs = qs.filter(
                    Q(name__icontains=search)
                    | Q(borrower__name__icontains=search)
                    | Q(borrower__email__icontains=search)
                )

            if filters.get("status"):
                qs = qs.filter(status=filters["status"])

            if filters.get("borrower_id"):
                qs = qs.filter(borrower_id=filters["borrower_id"])

            if filters.get("due_date_from"):
                qs = qs.filter(due_date__gte=filters["due_date_from"])

            if filters.get("due_date_to"):
                qs = qs.filter(due_date__lte=filters["due_date_to"])

            if filters.get("min_total_amount"):
                qs = qs.filter(total_amount__gte=filters["min_total_amount"])

            if filters.get("max_total_amount"):
                qs = qs.filter(total_amount__lte=filters["max_total_amount"])

        # Apply sorting
        sort_by = camel_to_snake(sort_by)
        if sort_order.lower() == "desc":
            sort_by = f"-{sort_by}"
        qs = qs.order_by(sort_by)

        # Paginate
        result = paginate_queryset(qs, page, limit)

        # Add stats to each debt
        for debt in result["data"]:
            debt.stats = DebtService._get_debt_stats(debt)

        return result

    @staticmethod
    def _get_debt_stats(debt):
        """
        Calculate aggregate statistics for a single debt.

        Args:
            debt: Debt instance

        Returns:
            dict: Statistics including payments, penalties, and balance info
        """
        # Get payment statistics
        payment_stats = PaymentTransaction.objects.filter(
            debt_id=debt.id, deleted_at__isnull=True
        ).aggregate(
            total_paid=Sum("amount"),
            payment_count=Count("id"),
            last_payment_date=Max("payment_date"),
        )

        # Get penalty statistics
        penalty_stats = PenaltyTransaction.objects.filter(
            debt_id=debt.id, deleted_at__isnull=True
        ).aggregate(total_penalty=Sum("amount"), penalty_count=Count("id"))

        total_paid = payment_stats.get("total_paid") or Decimal("0")
        total_penalty = penalty_stats.get("total_penalty") or Decimal("0")
        payment_count = payment_stats.get("payment_count") or 0
        penalty_count = penalty_stats.get("penalty_count") or 0
        last_payment_date = payment_stats.get("last_payment_date")

        remaining_balance = debt.total_amount - total_paid

        # Calculate days overdue
        days_overdue = 0
        if debt.due_date and remaining_balance > Decimal("0.01"):
            today = timezone.now().date()
            if debt.due_date < today:
                days_overdue = (today - debt.due_date).days

        return {
            "total_paid": total_paid,
            "total_penalty": total_penalty,
            "remaining_balance": remaining_balance,
            "days_overdue": days_overdue,
            "payment_count": payment_count,
            "penalty_count": penalty_count,
            "last_payment_date": last_payment_date,
            "is_fully_paid": remaining_balance <= Decimal("0.01"),
        }

    # ============================================================
    # WRITE OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new debt.

        Args:
            data: Dictionary containing debt data
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Debt: The created debt instance

        Raises:
            ValidationError: If validation fails
        """
        if not data.get("borrower_id"):
            raise ValidationError({"borrower_id": "Borrower ID is required."})
        
        logger.debug(f"Creating debt with data: {data}")
        # Validate borrower exists
        borrower = Borrower.objects.filter(id=data.get("borrower_id")).first()
        if not borrower:
            raise ValidationError({"borrower_id": "Borrower not found."})

        # Get default rates if not provided
        interest_rate = data.get("interest_rate")
        if interest_rate is None:
            interest_rate = default_interest_rate()

        penalty_rate = data.get("penalty_rate")
        if penalty_rate is None:
            penalty_rate = default_penalty_rate()

        interest_calculation_period = data.get(
            "interest_calculation_period", default_interest_calculation_period()
        )

        # Create debt
        debt = Debt.objects.create(
            borrower=borrower,
            name=data["name"],
            total_amount=data["total_amount"],
            paid_amount=data.get("paid_amount", Decimal("0")),
            due_date=data["due_date"],
            status=data.get("status", Debt.Status.ACTIVE),
            interest_rate=interest_rate,
            penalty_rate=penalty_rate,
            interest_calculation_period=interest_calculation_period,
            last_interest_accrual_date=data.get("last_interest_accrual_date"),
        )

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_create",
                model_name="Debt",
                object_id=str(debt.id),
                changes={"data": data},
            )

        logger.info(f"Debt created: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def update(debt_id, data, user=None, request=None):
        """
        Update an existing debt.

        Args:
            debt_id: ID of the debt to update
            data: Dictionary containing updated fields
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Debt: The updated debt instance

        Raises:
            ValidationError: If validation fails or debt not found
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        # If borrower is changing, validate new borrower
        if data.get("borrower_id") and data["borrower_id"] != debt.borrower_id:
            borrower = Borrower.objects.filter(id=data["borrower_id"]).first()
            if not borrower:
                raise ValidationError({"borrower_id": "Borrower not found."})
            debt.borrower = borrower

        # Track old values for audit
        old_data = {
            "total_amount": debt.total_amount,
            "paid_amount": debt.paid_amount,
            "status": debt.status,
        }

        # Update fields
        update_fields = [
            "name",
            "total_amount",
            "paid_amount",
            "due_date",
            "status",
            "interest_rate",
            "penalty_rate",
            "interest_calculation_period",
            "last_interest_accrual_date",
        ]
        for field in update_fields:
            if field in data:
                setattr(debt, field, data[field])

        # Validate paid amount doesn't exceed total amount
        if debt.paid_amount > debt.total_amount:
            raise ValidationError(
                {
                    "paid_amount": f"Paid amount (₱{debt.paid_amount:,.2f}) cannot exceed total amount (₱{debt.total_amount:,.2f})."
                }
            )

        # Recalculate remaining amount and save
        debt.save()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_update",
                model_name="Debt",
                object_id=str(debt.id),
                changes={
                    "before": old_data,
                    "after": {
                        "total_amount": debt.total_amount,
                        "paid_amount": debt.paid_amount,
                        "status": debt.status,
                    },
                },
            )

        logger.info(f"Debt updated: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def delete(debt_id, user=None, request=None):
        """
        Soft delete a debt.

        Args:
            debt_id: ID of the debt to delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Debt: The soft-deleted debt instance

        Raises:
            ValidationError: If debt not found or already deleted
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        if debt.deleted_at:
            raise ValidationError({"id": "Debt is already deleted."})

        debt.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_delete",
                model_name="Debt",
                object_id=str(debt.id),
                changes={"deleted_at": debt.deleted_at},
            )

        logger.info(f"Debt soft-deleted: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def restore(debt_id, user=None, request=None):
        """
        Restore a soft-deleted debt.

        Args:
            debt_id: ID of the debt to restore
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Debt: The restored debt instance

        Raises:
            ValidationError: If debt not found or not deleted
        """
        debt = Debt.objects.filter(id=debt_id).first()
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        if not debt.deleted_at:
            raise ValidationError({"id": "Debt is not deleted."})

        debt.restore()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_restore",
                model_name="Debt",
                object_id=str(debt.id),
                changes={"restored_at": timezone.now()},
            )

        logger.info(f"Debt restored: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def permanent_delete(debt_id, user=None, request=None):
        """
        Permanently delete a debt (hard delete).

        Args:
            debt_id: ID of the debt to permanently delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Raises:
            ValidationError: If debt not found
        """
        debt = Debt.objects.filter(id=debt_id).first()
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        # Check if debt has related records
        if debt.payments.exists():
            raise ValidationError(
                {
                    "detail": "Cannot permanently delete debt with existing payments. Void payments first."
                }
            )

        # Delete related records
        debt.penalties.all().delete()
        debt.agreements.all().delete()

        # Audit log before deletion
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_permanent_delete",
                model_name="Debt",
                object_id=str(debt.id),
                changes={"permanent": True},
            )

        debt.delete()

        logger.info(f"Debt permanently deleted: {debt_id}")

    # ============================================================
    # STATISTICS & REPORTS
    # ============================================================

    @staticmethod
    def get_statistics():
        """
        Get comprehensive debt statistics.

        Returns:
            dict: Statistics including counts by status and total amounts
        """
        qs = Debt.objects.filter(deleted_at__isnull=True)

        total_debts = qs.count()
        status_counts = qs.values("status").annotate(count=Count("id"))
        total_amount = qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
        remaining_amount = qs.aggregate(total=Sum("remaining_amount"))[
            "total"
        ] or Decimal("0")
        total_overdue_amount = qs.filter(status="overdue").aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

        # Build status counts dictionary
        status_stats = {}
        for item in status_counts:
            status_stats[item["status"]] = item["count"]

        return {
            "total_debts": total_debts,
            "total_active": status_stats.get(Debt.Status.ACTIVE, 0),
            "total_paid": status_stats.get(Debt.Status.PAID, 0),
            "total_overdue": status_stats.get(Debt.Status.OVERDUE, 0),
            "total_defaulted": status_stats.get(Debt.Status.DEFAULTED, 0),
            "total_amount_owed": total_amount,
            "total_remaining_balance": remaining_amount,
            "total_overdue_amount":  total_overdue_amount,
        }

    @staticmethod
    def get_aging_summary(as_of_date=None):
        """
        Get aging summary for accounts receivable.

        Args:
            as_of_date: Date to calculate aging (defaults to today)

        Returns:
            dict: Aging buckets with totals and percentages
        """
        if not as_of_date:
            as_of_date = timezone.now().date()

        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal("0.01"),
        ).select_related("borrower")

        buckets = {
            "0-30": {"total": Decimal("0"), "count": 0},
            "31-60": {"total": Decimal("0"), "count": 0},
            "61-90": {"total": Decimal("0"), "count": 0},
            "90+": {"total": Decimal("0"), "count": 0},
        }

        total_outstanding = Decimal("0")

        for debt in debts:
            if debt.due_date:
                days_past_due = (as_of_date - debt.due_date).days
                if days_past_due < 0:
                    days_past_due = 0

                if days_past_due <= 30:
                    bucket = "0-30"
                elif days_past_due <= 60:
                    bucket = "31-60"
                elif days_past_due <= 90:
                    bucket = "61-90"
                else:
                    bucket = "90+"

                buckets[bucket]["total"] += debt.remaining_amount
                buckets[bucket]["count"] += 1
                total_outstanding += debt.remaining_amount

        # Calculate percentages and build result
        result = []
        for key, data in buckets.items():
            percentage = (
                (data["total"] / total_outstanding * 100)
                if total_outstanding > 0
                else 0
            )
            result.append(
                {
                    "range": key,
                    "total_amount": data["total"],
                    "count": data["count"],
                    "percentage": round(percentage, 2),
                }
            )

        return {
            "as_of_date": as_of_date.isoformat(),
            "total_outstanding": total_outstanding,
            "buckets": result,
        }

    @staticmethod
    def get_collection_schedule(period_type="monthly", as_of_date=None):
        """
        Get collection schedule for active debts.

        Args:
            period_type: 'weekly', 'monthly', 'semi-annual', 'yearly'
            as_of_date: Reference date (defaults to today)

        Returns:
            dict: Collection schedule grouped by debtor with period amounts
        """
        from system_settings.utils import amortization_type

        # Convert as_of_date to date object if it's a string
        if as_of_date is None:
            as_of_date = timezone.now().date()
        elif isinstance(as_of_date, str):
            try:
                # datetime may be imported as the datetime class; use fromisoformat
                # and get the date portion to support both module and class imports
                as_of_date = datetime.fromisoformat(as_of_date).date()
            except ValueError:
                # If parsing fails, fallback to today
                as_of_date = timezone.now().date()

        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
        ).select_related("borrower")

        if not debts:
            return {
                "period_type": period_type,
                "as_of_date": as_of_date.isoformat(),
                "debtors": [],
                "total_due": Decimal("0"),
                "total_debtors": 0,
            }

        period_days_map = {
            "weekly": {"days": 7, "label": "Weekly"},
            "monthly": {"days": 30, "label": "Monthly"},
            "semi-annual": {"days": 182, "label": "Semi-Annual"},
            "yearly": {"days": 365, "label": "Yearly"},
        }

        period_info = period_days_map.get(period_type, {"days": 30, "label": "Monthly"})
        amort_type = amortization_type()

        # Calculate schedule for each debt
        schedule_items = []
        for debt in debts:
            if debt.remaining_amount <= Decimal("0.01"):
                continue

            # Calculate periodic payment
            total_days = (debt.due_date - debt.created_at.date()).days
            if total_days <= 0:
                total_days = 30

            total_periods = max(1, total_days // period_info["days"])

            # Calculate periodic payment based on amortization type
            if amort_type == "flat":
                # Simple division
                periodic_payment = debt.total_amount / total_periods
            else:
                # Declining balance (annuity formula)
                if debt.interest_rate and debt.interest_rate > 0:
                    periods_per_year = 365 / period_info["days"]
                    rate_per_period = debt.interest_rate / 100 / periods_per_year
                    if rate_per_period > 0:
                        factor = (1 + rate_per_period) ** total_periods
                        periodic_payment = (
                            debt.total_amount * rate_per_period * factor
                        ) / (factor - 1)
                    else:
                        periodic_payment = debt.total_amount / total_periods
                else:
                    periodic_payment = debt.total_amount / total_periods

            periodic_payment = round(periodic_payment, 2)

            # Calculate current period and due date
            days_since_start = (timezone.now().date() - debt.created_at.date()).days
            current_period = max(0, days_since_start // period_info["days"])
            next_due_date = debt.created_at.date() + timezone.timedelta(
                days=(current_period + 1) * period_info["days"]
            )

            # Calculate total paid in period
            period_start = debt.created_at.date() + timezone.timedelta(
                days=current_period * period_info["days"]
            )
            period_end = debt.created_at.date() + timezone.timedelta(
                days=(current_period + 1) * period_info["days"]
            )

            total_paid_in_period = PaymentTransaction.objects.filter(
                debt_id=debt.id,
                deleted_at__isnull=True,
                payment_date__gte=period_start,
                payment_date__lt=period_end,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            is_paid = total_paid_in_period >= periodic_payment - Decimal("0.05")

            if debt.remaining_amount > Decimal("0.01"):
                schedule_items.append(
                    {
                        "debt_id": debt.id,
                        "debt_name": debt.name,
                        "borrower_id": debt.borrower.id,
                        "borrower_name": debt.borrower.name,
                        "period_amount": periodic_payment,
                        "total_paid_in_period": total_paid_in_period,
                        "is_paid": is_paid,
                        "next_due_date": next_due_date.isoformat(),
                        "remaining_balance": debt.remaining_amount,
                        "contact": debt.borrower.contact,
                        "email": debt.borrower.email,
                    }
                )

        # Group by debtor
        debtor_map = {}
        for item in schedule_items:
            debtor_id = item["borrower_id"]
            if debtor_id not in debtor_map:
                debtor_map[debtor_id] = {
                    "borrower_id": debtor_id,
                    "borrower_name": item["borrower_name"],
                    "contact": item["contact"],
                    "email": item["email"],
                    "debts": [],
                    "total_period_amount": Decimal("0"),
                    "total_paid_in_period": Decimal("0"),
                    "all_paid": True,
                }

            debtor = debtor_map[debtor_id]
            debtor["debts"].append(item)
            debtor["total_period_amount"] += item["period_amount"]
            debtor["total_paid_in_period"] += item["total_paid_in_period"]
            if not item["is_paid"]:
                debtor["all_paid"] = False

        debtors = list(debtor_map.values())
        total_due = sum(d["total_period_amount"] for d in debtors)

        return {
            "period_type": period_type,
            "period_label": period_info["label"],
            "amortization_type": amort_type,
            "as_of_date": as_of_date.isoformat(),
            "debtors": debtors,
            "total_due": total_due,
            "total_debtors": len(debtors),
        }

    # ============================================================
    # BULK OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def bulk_create(debts_data, user=None, request=None):
        """
        Bulk create multiple debts.

        Args:
            debts_data: List of debt data dictionaries
            user: User performing the action
            request: HTTP request object

        Returns:
            dict: {'created': list of created debts, 'errors': list of errors}
        """
        results = {"created": [], "errors": []}

        for data in debts_data:
            try:
                # Validate borrower exists
                borrower_id = data.get("borrower_id")
                if not borrower_id:
                    raise ValidationError({"borrower_id": "Borrower ID is required."})

                borrower = Borrower.objects.filter(
                    id=borrower_id, deleted_at__isnull=True
                ).first()
                if not borrower:
                    raise ValidationError(
                        {"borrower_id": f"Borrower with id {borrower_id} not found."}
                    )

                debt = DebtService.create(data, user, request)
                results["created"].append(debt)
            except Exception as e:
                results["errors"].append({"debt": data, "error": str(e)})

        return results

    @staticmethod
    @transaction.atomic
    def bulk_update(updates, user=None, request=None):
        """
        Bulk update multiple debts.

        Args:
            updates: List of dicts with 'id' and 'updates' keys
            user: User performing the action
            request: HTTP request object

        Returns:
            dict: {'updated': list of updated debts, 'errors': list of errors}
        """
        results = {"updated": [], "errors": []}

        for item in updates:
            try:
                debt_id = item.get("id")
                data = item.get("updates", {})

                if not debt_id:
                    raise ValidationError({"id": "Debt ID is required."})

                updated = DebtService.update(debt_id, data, user, request)
                results["updated"].append(updated)
            except Exception as e:
                results["errors"].append(
                    {
                        "id": item.get("id"),
                        "updates": item.get("updates", {}),
                        "error": str(e),
                    }
                )

        return results

    @staticmethod
    @transaction.atomic
    def correct_total_amount(debt_id, new_total_amount, user=None, request=None):
        """
        Correct total amount (data entry correction only - no forgiveness flow).

        Args:
            debt_id: ID of the debt
            new_total_amount: New total amount
            user: User performing the action
            request: HTTP request object

        Returns:
            Debt: The updated debt instance
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        if debt.deleted_at:
            raise ValidationError({"id": "Cannot update a deleted debt."})

        # Ensure new total is not less than paid amount
        new_total = Decimal(str(new_total_amount))
        if new_total < debt.paid_amount:
            raise ValidationError(
                {
                    "new_total_amount": f"New total amount (₱{new_total:,.2f}) cannot be less than paid amount (₱{debt.paid_amount:,.2f})."
                }
            )

        old_total = debt.total_amount
        debt.total_amount = new_total
        debt.save()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_correct_total",
                model_name="Debt",
                object_id=str(debt.id),
                changes={
                    "before": {"total_amount": old_total},
                    "after": {"total_amount": debt.total_amount},
                },
            )

        logger.info(
            f"Debt total corrected: {debt.id} - {debt.name} (₱{old_total} → ₱{new_total})"
        )
        return debt

    @staticmethod
    @transaction.atomic
    def recalculate_remaining(debt_id, user=None, request=None):
        """
        Recalculate remaining amount based on paid amount.

        Args:
            debt_id: ID of the debt
            user: User performing the action
            request: HTTP request object

        Returns:
            Debt: The updated debt instance
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        old_remaining = debt.remaining_amount
        debt.save()  # Triggers auto-calculation of remaining_amount

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_recalculate_remaining",
                model_name="Debt",
                object_id=str(debt.id),
                changes={
                    "before": {"remaining_amount": old_remaining},
                    "after": {"remaining_amount": debt.remaining_amount},
                },
            )

        logger.info(
            f"Debt remaining recalculated: {debt.id} - {debt.name} (₱{old_remaining} → ₱{debt.remaining_amount})"
        )
        return debt

    @staticmethod
    @transaction.atomic
    def apply_forgiveness(
        debt_id, amount_forgiven, user=None, request=None, reason=None
    ):
        """
        Apply forgiveness to a debt. Creates a ForgivenessLog entry.

        Args:
            debt_id: ID of the debt
            amount_forgiven: Amount to forgive
            user: User performing the action
            request: HTTP request object
            reason: Reason for forgiveness

        Returns:
            Debt: The updated debt instance
        """
        from debts.services.forgiveness import ForgivenessService

        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({"id": "Debt not found."})

        if debt.deleted_at:
            raise ValidationError({"id": "Cannot forgive a deleted debt."})

        if debt.remaining_amount <= Decimal("0.01"):
            raise ValidationError({"detail": "Debt is already fully paid."})

        amount = Decimal(str(amount_forgiven))
        if amount <= 0:
            raise ValidationError(
                {"amount_forgiven": "Forgiveness amount must be greater than 0."}
            )

        if amount > debt.remaining_amount:
            raise ValidationError(
                {
                    "amount_forgiven": f"Forgiveness amount (₱{amount:,.2f}) cannot exceed remaining amount (₱{debt.remaining_amount:,.2f})."
                }
            )

        # Use ForgivenessService to apply forgiveness
        ForgivenessService.apply_forgiveness(
            debt_id=debt_id,
            borrower_id=debt.borrower_id,
            amount=amount,
            created_by=user.username if user else "system",
            reason=reason,
            user=user,
            request=request,
        )

        # Refresh debt instance
        debt.refresh_from_db()

        return debt

    @staticmethod
    @transaction.atomic
    def mark_period_paid(
        borrower_id, period_type, payment_date, method_id, user=None, request=None
    ):
        """
        Mark all debts for a borrower in a period as paid.

        Args:
            borrower_id: ID of the borrower
            period_type: 'weekly', 'monthly', 'semi-annual', 'yearly'
            payment_date: Date of payment (YYYY-MM-DD)
            method_id: ID of the payment method
            user: User performing the action
            request: HTTP request object

        Returns:
            dict: {'payments': list of created payments, 'count': number of payments}
        """
        from payments.models.payment_transaction import PaymentTransaction
        from payments.services.payment_transaction import PaymentTransactionService

        borrower = Borrower.objects.filter(
            id=borrower_id, deleted_at__isnull=True
        ).first()
        if not borrower:
            raise ValidationError({"borrower_id": "Borrower not found."})

        # Get active debts for borrower
        debts = Debt.objects.filter(
            borrower_id=borrower_id,
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal("0.01"),
        )

        if not debts.exists():
            return {"payments": [], "count": 0}

        # Calculate period amounts using collection schedule
        try:
            schedule = DebtService.get_collection_schedule(period_type, payment_date)
            debtor_schedule = next(
                (
                    d
                    for d in schedule.get("debtors", [])
                    if d["borrower_id"] == borrower_id
                ),
                None,
            )
        except Exception as e:
            logger.warning(
                f"[MarkPeriodPaid] Schedule generation failed: {e}, falling back to manual"
            )
            debtor_schedule = None

        # If no schedule found for this debtor, create payments for all active debts using fallback
        if not debtor_schedule or not debtor_schedule.get("debts"):
            payments = []
            for debt in debts:
                if debt.remaining_amount <= Decimal("0.01"):
                    continue

                # Fallback divisor based on period type
                if period_type == "weekly":
                    divisor = 52
                elif period_type == "monthly":
                    divisor = 12
                elif period_type == "semi-annual":
                    divisor = 2
                else:  # yearly
                    divisor = 1

                amount = round(debt.remaining_amount / divisor, 2)
                if amount <= 0:
                    continue

                # Cap amount to remaining balance
                if amount > debt.remaining_amount:
                    amount = debt.remaining_amount

                payment_data = {
                    "debt_id": debt.id,
                    "method_id": method_id,
                    "amount": amount,
                    "payment_date": payment_date,
                    "reference": f"PERIOD_PAY_{period_type}_{payment_date}_{debt.id}",
                    "notes": f"Period payment for borrower {borrower.name}",
                }

                payment = PaymentTransactionService.create(
                    data=payment_data, user=user, request=request
                )
                payments.append(payment)

            return {"payments": payments, "count": len(payments)}

        # Use the schedule if found
        payments = []
        for debt_item in debtor_schedule.get("debts", []):
            if debt_item["is_paid"]:
                continue

            debt_id = debt_item["debt_id"]
            amount = debt_item["period_amount"]

            if amount <= 0:
                continue

            # Get fresh debt instance to check remaining balance
            debt = Debt.objects.filter(id=debt_id, deleted_at__isnull=True).first()
            if not debt or debt.remaining_amount <= Decimal("0.01"):
                continue

            # ✅ CRITICAL FIX: Cap amount to remaining balance
            if amount > debt.remaining_amount:
                amount = debt.remaining_amount

            payment_data = {
                "debt_id": debt_id,
                "method_id": method_id,
                "amount": amount,
                "payment_date": payment_date,
                "reference": f"PERIOD_PAY_{period_type}_{payment_date}",
                "notes": f"Automated {period_type} payment for borrower {borrower.name}",
            }

            payment = PaymentTransactionService.create(
                data=payment_data, user=user, request=request
            )
            payments.append(payment)

        return {"payments": payments, "count": len(payments)}

    @staticmethod
    @transaction.atomic
    def fix_precision(debt_id=None):
        """
        Fix floating point precision for debts.

        Args:
            debt_id: Optional specific debt ID. If None, fixes all debts.

        Returns:
            dict: {'fixed': number of debts fixed}
        """
        qs = Debt.objects.filter(deleted_at__isnull=True)
        if debt_id:
            qs = qs.filter(id=debt_id)

        fixed_count = 0
        for debt in qs:
            original_remaining = debt.remaining_amount
            debt.save()  # Triggers auto-calculation
            if debt.remaining_amount != original_remaining:
                fixed_count += 1

        return {"fixed": fixed_count}

    # ============================================================
    # DEBTS IN BUCKET
    # ============================================================

    @staticmethod
    def get_debts_in_bucket(bucket_range, as_of_date, page=1, limit=10):
        """
        Get debts in a specific aging bucket with pagination.

        Args:
            bucket_range: e.g., '0-30 days', '31-60 days', '61-90 days', '90+ days'
            as_of_date: Date to calculate aging (YYYY-MM-DD)
            page: Page number
            limit: Items per page

        Returns:
            dict: {'data': list of debts, 'pagination': pagination metadata}
        """
        import datetime

        if isinstance(as_of_date, str):
            as_of_date = datetime.date.fromisoformat(as_of_date)

        # Parse bucket range
        if bucket_range == "90+ days":
            min_days = 90
            max_days = None
        else:
            parts = bucket_range.split("-")
            min_days = int(parts[0])
            max_days = int(parts[1].split()[0])

        # Get all active/overdue debts
        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal("0.01"),
        ).select_related("borrower")

        # Filter by days past due
        filtered = []
        for debt in debts:
            if debt.due_date:
                days_past_due = (as_of_date - debt.due_date).days
                if days_past_due < 0:
                    days_past_due = 0

                if max_days:
                    if min_days <= days_past_due <= max_days:
                        filtered.append(debt)
                else:
                    if days_past_due >= min_days:
                        filtered.append(debt)

        # Paginate
        total = len(filtered)
        start = (page - 1) * limit
        end = start + limit
        paginated = filtered[start:end]

        return {
            "data": paginated,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if total > 0 else 0,
            },
        }

    # ============================================================
    # EXPORT
    # ============================================================

    @staticmethod
    def export_debts(filters=None):
        """
        Export debts data for reporting.

        Args:
            filters: Optional filters

        Returns:
            list: List of debt dictionaries with selected fields
        """
        qs = Debt.objects.filter(deleted_at__isnull=True).select_related("borrower")

        if filters:
            if filters.get("status"):
                qs = qs.filter(status=filters["status"])
            if filters.get("borrower_id"):
                qs = qs.filter(borrower_id=filters["borrower_id"])

        export_data = []
        for debt in qs:
            export_data.append(
                {
                    "id": debt.id,
                    "borrower_name": debt.borrower.name,
                    "borrower_contact": debt.borrower.contact,
                    "borrower_email": debt.borrower.email,
                    "name": debt.name,
                    "total_amount": float(debt.total_amount),
                    "paid_amount": float(debt.paid_amount),
                    "remaining_amount": float(debt.remaining_amount),
                    "due_date": debt.due_date.isoformat(),
                    "status": debt.status,
                    "interest_rate": (
                        float(debt.interest_rate) if debt.interest_rate else None
                    ),
                    "penalty_rate": (
                        float(debt.penalty_rate) if debt.penalty_rate else None
                    ),
                    "created_at": debt.created_at.isoformat(),
                }
            )

        return export_data

    # ============================================================
    # IMPORT FROM CSV
    # ============================================================

    @staticmethod
    @transaction.atomic
    def import_from_csv(file_path, user=None, request=None):
        from borrowers.services.borrower import BorrowerService

        """
        Import debts from CSV file.
        
        Args:
            file_path: Path to CSV file
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'imported': list of imported debts, 'errors': list of errors}
        """
        import csv
        from io import StringIO

        results = {"imported": [], "errors": []}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            reader = csv.DictReader(StringIO(content))
            row_number = 1

            for row in reader:
                row_number += 1
                try:
                    # Validate required fields
                    borrower_name = row.get("borrower_name") or row.get("borrower")
                    if not borrower_name:
                        raise ValidationError(
                            {"borrower_name": "Borrower name is required."}
                        )

                    # Find or create borrower
                    borrower = Borrower.objects.filter(
                        name__iexact=borrower_name, deleted_at__isnull=True
                    ).first()

                    if not borrower:
                        # Create borrower if not exists
                        borrower_data = {
                            "name": borrower_name,
                            "contact": row.get("borrower_contact"),
                            "email": row.get("borrower_email"),
                            "address": row.get("borrower_address"),
                        }
                        borrower = BorrowerService.create(borrower_data, user, request)

                    # Prepare debt data
                    debt_data = {
                        "borrower_id": borrower.id,
                        "name": row.get("name", f"Loan for {borrower_name}"),
                        "total_amount": Decimal(row.get("total_amount", 0)),
                        "paid_amount": Decimal(row.get("paid_amount", 0)),
                        "due_date": row.get("due_date"),
                        "status": row.get("status", Debt.Status.ACTIVE),
                        "interest_rate": (
                            row.get("interest_rate")
                            if row.get("interest_rate")
                            else None
                        ),
                        "penalty_rate": (
                            row.get("penalty_rate") if row.get("penalty_rate") else None
                        ),
                    }

                    debt = DebtService.create(debt_data, user, request)
                    results["imported"].append(debt)

                except Exception as e:
                    results["errors"].append(
                        {"row": row_number, "data": row, "error": str(e)}
                    )

            return results

        except Exception as e:
            raise ValidationError({"file": f"Failed to read CSV: {str(e)}"})

    @staticmethod
    def mark_overdue_debts():
        """
        Automatically mark debts as overdue when due date passes.
        Should be run daily via scheduler.

        Returns:
            dict: {'count': number of debts marked overdue}
        """
        from django.utils import timezone

        today = timezone.now().date()

        # Find active debts that are past due
        overdue_debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status=Debt.Status.ACTIVE,
            due_date__lt=today,
            remaining_amount__gt=Decimal("0.01"),
        )

        count = overdue_debts.count()

        # Update status
        overdue_debts.update(status=Debt.Status.OVERDUE, updated_at=timezone.now())

        logger.info(f"Marked {count} debts as overdue")

        # Optionally log each change for audit
        for debt in overdue_debts:
            log_audit_event(
                request=None,
                user=None,
                action_type="debt_mark_overdue",
                model_name="Debt",
                object_id=str(debt.id),
                changes={"status": Debt.Status.OVERDUE},
            )

        return {"count": count}

    @staticmethod
    def has_active_debts(borrower_id):
        """
        Check if a borrower has active debts.

        Args:
            borrower_id: ID of the borrower

        Returns:
            bool: True if borrower has active debts
        """
        return Debt.objects.filter(
            borrower_id=borrower_id,
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal("0.01"),
        ).exists()

    @staticmethod
    def get_total_remaining_for_borrower(borrower_id):
        """
        Get total remaining amount for a borrower.

        Args:
            borrower_id: ID of the borrower

        Returns:
            Decimal: Total remaining amount
        """
        result = Debt.objects.filter(
            borrower_id=borrower_id,
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
        ).aggregate(total=Sum("remaining_amount"))

        return result["total"] or Decimal("0")

    @staticmethod
    def get_debts_by_borrower(borrower_id, include_deleted=False, page=1, limit=20):
        """
        Get paginated list of debts for a specific borrower.

        Args:
            borrower_id: ID of the borrower
            include_deleted: Whether to include soft-deleted debts
            page: Page number
            limit: Items per page

        Returns:
            dict: Paginated list of debts
        """
        return DebtService.get_list(
            filters={"borrower_id": borrower_id, "include_deleted": include_deleted},
            page=page,
            limit=limit,
            sort_by="due_date",
            sort_order="asc",
        )

    @staticmethod
    def exists_for_borrower(borrower_id, debt_name):
        """
        Check if a debt with given name exists for a borrower.

        Args:
            borrower_id: ID of the borrower
            debt_name: Name of the debt to check

        Returns:
            bool: True if debt exists
        """
        return Debt.objects.filter(
            borrower_id=borrower_id, name=debt_name, deleted_at__isnull=True
        ).exists()

    @staticmethod
    def _get_period_info(period_type):
        """
        Get period information based on period type.

        Args:
            period_type: 'weekly', 'monthly', 'semi-annual', 'yearly'

        Returns:
            dict: {'days': int, 'label': str}
        """
        period_map = {
            "weekly": {"days": 7, "label": "Weekly"},
            "monthly": {"days": 30, "label": "Monthly"},
            "semi-annual": {"days": 182, "label": "Semi-Annual"},
            "yearly": {"days": 365, "label": "Yearly"},
        }
        return period_map.get(period_type, {"days": 30, "label": "Monthly"})
