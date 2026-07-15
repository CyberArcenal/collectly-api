import logging
from django.db import transaction
from django.core.exceptions import ValidationError

from audit.utils.log import log_audit_event
from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class PaymentMethodService:
    """
    Service layer for PaymentMethod and PaymentMethodStat operations.
    """

    # ============================================================
    # PAYMENT METHOD CRUD
    # ============================================================

    @staticmethod
    def get_by_id(method_id):
        """
        Get a single payment method by ID.
        """
        try:
            return PaymentMethod.objects.get(id=method_id, deleted_at__isnull=True)
        except PaymentMethod.DoesNotExist:
            return None

    @staticmethod
    def get_list(page=1, limit=20):
        """
        Get paginated list of payment methods.
        """
        qs = PaymentMethod.objects.filter(deleted_at__isnull=True).order_by('-is_default', 'name')
        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_default():
        """
        Get the default payment method.
        """
        return PaymentMethod.objects.filter(
            is_default=True,
            deleted_at__isnull=True
        ).first()

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new payment method.
        """
        # Validate unique name
        if PaymentMethod.objects.filter(name=data['name']).exists():
            raise ValidationError({'name': 'Payment method already exists.'})
        
        is_default = data.get('is_default', False)
        
        # If this is default, remove other defaults
        if is_default:
            PaymentMethod.objects.filter(is_default=True).update(is_default=False)
        
        method = PaymentMethod.objects.create(
            name=data['name'],
            description=data.get('description'),
            icon=data.get('icon', 'CreditCard'),
            is_default=is_default
        )
        
        # Create stats record
        PaymentMethodStat.objects.create(method=method)
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_method_create',
                model_name='PaymentMethod',
                object_id=str(method.id),
                changes={'data': data}
            )
        
        logger.info(f"Payment method created: {method.id} - {method.name}")
        return method

    @staticmethod
    @transaction.atomic
    def update(method_id, data, user=None, request=None):
        """
        Update a payment method.
        """
        method = PaymentMethodService.get_by_id(method_id)
        if not method:
            raise ValidationError({'id': 'Payment method not found.'})
        
        # Check unique name if changed
        if data.get('name') and data['name'] != method.name:
            if PaymentMethod.objects.filter(name=data['name']).exists():
                raise ValidationError({'name': 'Payment method already exists.'})
            method.name = data['name']
        
        if 'description' in data:
            method.description = data['description']
        if 'icon' in data:
            method.icon = data['icon']
        
        # If setting as default, remove other defaults
        if data.get('is_default', False) and not method.is_default:
            PaymentMethod.objects.filter(is_default=True).update(is_default=False)
            method.is_default = True
        
        method.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_method_update',
                model_name='PaymentMethod',
                object_id=str(method.id),
                changes={'data': data}
            )
        
        logger.info(f"Payment method updated: {method.id} - {method.name}")
        return method

    @staticmethod
    @transaction.atomic
    def set_default(method_id, user=None, request=None):
        """
        Set a payment method as default.
        """
        method = PaymentMethodService.get_by_id(method_id)
        if not method:
            raise ValidationError({'id': 'Payment method not found.'})
        
        # Remove other defaults
        PaymentMethod.objects.filter(is_default=True).update(is_default=False)
        
        method.is_default = True
        method.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_method_set_default',
                model_name='PaymentMethod',
                object_id=str(method.id),
                changes={'is_default': True}
            )
        
        logger.info(f"Payment method set as default: {method.id} - {method.name}")
        return method

    @staticmethod
    @transaction.atomic
    def delete(method_id, user=None, request=None):
        """
        Soft delete a payment method (cannot delete default).
        """
        method = PaymentMethodService.get_by_id(method_id)
        if not method:
            raise ValidationError({'id': 'Payment method not found.'})
        
        if method.is_default:
            raise ValidationError({'id': 'Cannot delete the default payment method.'})
        
        # Delete stats first
        if hasattr(method, 'stats'):
            method.stats.delete()
        
        method.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_method_delete',
                model_name='PaymentMethod',
                object_id=str(method.id),
                changes={'deleted_at': method.deleted_at}
            )
        
        logger.info(f"Payment method soft-deleted: {method.id} - {method.name}")
        return method

    # ============================================================
    # PAYMENT METHOD STATS
    # ============================================================

    @staticmethod
    def get_stats(method_id):
        """
        Get stats for a payment method.
        """
        method = PaymentMethodService.get_by_id(method_id)
        if not method:
            raise ValidationError({'id': 'Payment method not found.'})
        
        if not hasattr(method, 'stats'):
            # Create stats if missing
            method.stats = PaymentMethodStat.objects.create(method=method)
        
        return method.stats

    @staticmethod
    @transaction.atomic
    def increment_stats(method_id, amount):
        """
        Increment stats for a payment method.
        """
        method = PaymentMethodService.get_by_id(method_id)
        if not method:
            return
        
        stats = PaymentMethodService.get_stats(method_id)
        stats.increment(amount)
        
        logger.debug(f"Stats incremented for method {method_id}: +{amount}")
        return stats

    @staticmethod
    @transaction.atomic
    def decrement_stats(method_id, amount):
        """
        Decrement stats for a payment method (e.g., when payment is voided).
        """
        method = PaymentMethodService.get_by_id(method_id)
        if not method:
            return
        
        stats = PaymentMethodService.get_stats(method_id)
        stats.decrement(amount)
        
        logger.debug(f"Stats decremented for method {method_id}: -{amount}")
        return stats

    @staticmethod
    def get_all_stats():
        """
        Get stats for all payment methods.
        """
        methods = PaymentMethod.objects.filter(deleted_at__isnull=True)
        stats_list = []
        
        for method in methods:
            stats = PaymentMethodService.get_stats(method.id)
            stats_list.append({
                'method': {
                    'id': method.id,
                    'name': method.name,
                    'icon': method.icon,
                },
                'transaction_count': stats.transaction_count,
                'total_amount': stats.total_amount,
                'average_transaction': stats.average_transaction,
            })
        
        return stats_list
    

    @staticmethod
    def get_overall_summary():
        """
        Get overall summary statistics for all payment methods.

        Returns:
            dict: {
                'total_methods': int,
                'total_transactions': int,
                'total_amount_collected': float,
                'default_method': dict | None,
                'methods': list of method stats
            }
        """
        methods = PaymentMethod.objects.filter(deleted_at__isnull=True)
        total_methods = methods.count()

        # Get default method
        default_method = methods.filter(is_default=True).first()

        # Prefetch stats to avoid N+1 queries
        methods_with_stats = methods.prefetch_related('stats')

        method_stats = []
        total_transactions = 0
        total_amount_collected = 0

        for method in methods_with_stats:
            stats = getattr(method, 'stats', None)
            transaction_count = stats.transaction_count if stats else 0
            total_amount = float(stats.total_amount) if stats else 0

            total_transactions += transaction_count
            total_amount_collected += total_amount

            method_stats.append({
                'id': method.id,
                'name': method.name,
                'icon': method.icon,
                'is_default': method.is_default,
                'transaction_count': transaction_count,
                'total_amount': total_amount,
                'average_transaction': round(
                    total_amount / transaction_count if transaction_count > 0 else 0,
                    2
                ),
            })

        return {
            'total_methods': total_methods,
            'total_transactions': total_transactions,
            'total_amount_collected': round(total_amount_collected, 2),
            'default_method': {
                'id': default_method.id,
                'name': default_method.name,
                'icon': default_method.icon,
            } if default_method else None,
            'methods': method_stats,
        }