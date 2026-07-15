import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from debts.serializers.interest_rate_change_log import (
    InterestRateChangeLogReadSerializer,
    InterestRateChangeLogListSerializer,
    InterestRateChangeLogCreateSerializer,
)
from debts.services.interest_rate_change import InterestRateChangeService
from users.permissions.base import IsAccountActive, can_read, can_edit
from utils.helpers import filter_cleaner
from utils.response import BasePaginatedSerializer, CustomPagination, _success, _error
from utils.security import get_client_ip

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------

class InterestRateChangeLogListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = InterestRateChangeLogListSerializer(many=True)


class InterestRateChangeLogDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = InterestRateChangeLogReadSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class InterestRateChangeLogCRUDView(APIView):
    """
    CRUD operations for interest rate change logs.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /interest-rate-changes/  (list) and GET /interest-rate-changes/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Interest Rate Changes"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="setting_key", type=str, description="Filter by setting key", required=False),
            OpenApiParameter(name="loan_id", type=int, description="Filter by loan ID", required=False),
            OpenApiParameter(name="changed_by", type=str, description="Filter by changer", required=False),
            OpenApiParameter(name="from_date", type=str, description="Filter from date", required=False),
            OpenApiParameter(name="to_date", type=str, description="Filter to date", required=False),
        ],
        responses={
            200: InterestRateChangeLogListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single interest rate change log (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single interest rate change log or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view interest rate changes."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                log_entry = InterestRateChangeService.get_by_id(id)
                if not log_entry:
                    return _error(
                        data={"detail": "Interest rate change log not found."},
                        message="Interest rate change log not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = InterestRateChangeLogReadSerializer(log_entry, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="InterestRateChangeLog",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Interest rate change log retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'setting_key': request.query_params.get('setting_key'),
                'loan_id': request.query_params.get('loan_id'),
                'changed_by': request.query_params.get('changed_by'),
                'from_date': request.query_params.get('from_date'),
                'to_date': request.query_params.get('to_date'),
            }
            filters = filter_cleaner(filters)

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))

            result = InterestRateChangeService.get_list(
                filters=filters,
                page=page,
                limit=limit
            )

            serialized_data = InterestRateChangeLogListSerializer(
                result['data'],
                many=True,
                context={'request': request}
            ).data

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Interest rate change logs retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="InterestRateChangeLog",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Interest rate change log retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /interest-rate-changes/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Interest Rate Changes"],
        request=InterestRateChangeLogCreateSerializer,
        responses={
            201: InterestRateChangeLogDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new interest rate change log (system or per-loan). Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new interest rate change log."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create interest rate change logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = InterestRateChangeLogCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="InterestRateChangeLog",
                object_id="new",
                changes={"error": serializer.errors, "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = serializer.validated_data
            log_entry = InterestRateChangeService.create_log(
                setting_key=data['setting_key'],
                old_value=data.get('old_value'),
                new_value=data.get('new_value'),
                changed_by=data.get('changed_by', user.username if user else 'system'),
                reason=data.get('reason'),
                loan_id=data.get('loan', {}).id if data.get('loan') else None,
                user=user,
                request=request
            )

            read_serializer = InterestRateChangeLogReadSerializer(log_entry, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Interest rate change log created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Interest rate change log creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create interest rate change log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /interest-rate-changes/<id>/
    # NOT ALLOWED - Logs are immutable
    # ------------------------------------------------------------------

    def put(self, request, id=None):
        return _error(
            data={"detail": "Interest rate change logs are immutable."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # PATCH /interest-rate-changes/<id>/
    # NOT ALLOWED - Logs are immutable
    # ------------------------------------------------------------------

    def patch(self, request, id=None):
        return _error(
            data={"detail": "Interest rate change logs are immutable."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # DELETE /interest-rate-changes/<id>/
    # NOT ALLOWED - Logs are immutable
    # ------------------------------------------------------------------

    def delete(self, request, id=None):
        return _error(
            data={"detail": "Interest rate change logs are immutable."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        


# ============================================================
# Serializers for Documentation
# ============================================================

class MostFrequentSettingSerializer(serializers.Serializer):
    setting_key = serializers.CharField()
    count = serializers.IntegerField()


class UserChangeCountSerializer(serializers.Serializer):
    user = serializers.CharField()
    count = serializers.IntegerField()


class LoanChangeCountSerializer(serializers.Serializer):
    loan_id = serializers.IntegerField()
    count = serializers.IntegerField()


class InterestRateChangeStatsDataSerializer(serializers.Serializer):
    total_changes = serializers.IntegerField()
    most_frequent_setting = MostFrequentSettingSerializer(allow_null=True)
    changes_by_user = UserChangeCountSerializer(many=True)
    changes_by_loan = LoanChangeCountSerializer(many=True)
    average_change_magnitude = serializers.FloatField()
    max_change_magnitude = serializers.FloatField()
    min_change_magnitude = serializers.FloatField()
    changes_last_30_days = serializers.IntegerField()


class InterestRateChangeStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = InterestRateChangeStatsDataSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ============================================================
# Statistics View
# ============================================================

class InterestRateChangeLogStatisticsView(APIView):
    """
    Get statistics for interest rate change logs.

    Returns comprehensive statistics including:
    - Total number of changes
    - Most frequently changed setting
    - Changes grouped by user
    - Changes grouped by loan
    - Change magnitude statistics (average, max, min)
    - Changes in the last 30 days
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Interest Rate Changes"],
        summary="Get interest rate change statistics",
        description="Returns comprehensive statistics about interest rate changes including user activity and change magnitudes.",
        parameters=[
            OpenApiParameter(
                name="start_date",
                type=str,
                description="Filter changes from this date (YYYY-MM-DD)",
                required=False,
            ),
            OpenApiParameter(
                name="end_date",
                type=str,
                description="Filter changes up to this date (YYYY-MM-DD)",
                required=False,
            ),
        ],
        responses={
            200: InterestRateChangeStatsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                name="Success Response",
                value={
                    "status": True,
                    "message": "Statistics retrieved successfully.",
                    "data": {
                        "total_changes": 45,
                        "most_frequent_setting": {
                            "setting_key": "default_interest_rate",
                            "count": 12
                        },
                        "changes_by_user": [
                            {"user": "admin", "count": 30},
                            {"user": "manager", "count": 10},
                            {"user": "system", "count": 5}
                        ],
                        "changes_by_loan": [
                            {"loan_id": 15, "count": 3},
                            {"loan_id": 22, "count": 2}
                        ],
                        "average_change_magnitude": 0.75,
                        "max_change_magnitude": 2.50,
                        "min_change_magnitude": 0.10,
                        "changes_last_30_days": 12
                    }
                },
                status_codes=["200"],
            ),
            OpenApiExample(
                name="Unauthorized",
                value={"status": False, "message": "Authentication credentials were not provided.", "data": None},
                status_codes=["401"],
            ),
            OpenApiExample(
                name="Forbidden",
                value={"status": False, "message": "You do not have permission to view interest rate changes.", "data": None},
                status_codes=["403"],
            ),
        ],
    )
    def get(self, request):
        """Get interest rate change statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view interest rate changes."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            start_date = request.query_params.get('start_date')
            end_date = request.query_params.get('end_date')

            stats = InterestRateChangeService.get_statistics(
                start_date=start_date,
                end_date=end_date
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="InterestRateChangeLog",
                object_id="stats",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Interest rate change stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve interest rate change statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )