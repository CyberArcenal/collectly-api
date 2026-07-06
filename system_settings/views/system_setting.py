import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated, AllowAny

from audit.utils.log import log_audit_event
from system_settings.models.system_setting import SystemSetting, SettingType
from system_settings.serializers.system_setting import (
    SystemSettingReadSerializer,
    SystemSettingListSerializer,
    SystemSettingCreateSerializer,
    SystemSettingUpdateSerializer,
    SystemSettingBulkUpdateSerializer,
)
from system_settings.services.setting import SystemSettingService
from users.permissions.base import IsAccountActive, can_read, can_edit, is_admin
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

class SystemSettingListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = SystemSettingListSerializer(many=True)


class SystemSettingDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = SystemSettingReadSerializer()


class SystemSettingCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = SystemSettingReadSerializer()


class SystemSettingUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = SystemSettingReadSerializer()


class SystemSettingDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class SystemSettingGroupedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class SystemSettingPublicResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class SystemSettingSystemInfoResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class SystemSettingBulkUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class SystemSettingCRUDView(APIView):
    """
    CRUD operations for system settings.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /settings/  (list) and GET /settings/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["System Settings"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="setting_type", type=str, description="Filter by setting type", required=False),
            OpenApiParameter(name="is_public", type=bool, description="Filter by public status", required=False),
            OpenApiParameter(name="search", type=str, description="Search by key or value", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: SystemSettingListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single system setting (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single system setting or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                setting = SystemSettingService.get_by_id(id, include_deleted)
                if not setting:
                    return _error(
                        data={"detail": "System setting not found."},
                        message="System setting not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = SystemSettingReadSerializer(setting, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="SystemSetting",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="System setting retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'setting_type': request.query_params.get('setting_type'),
                'is_public': request.query_params.get('is_public'),
                'search': request.query_params.get('search'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            filters = filter_cleaner(filters)

            # Convert is_public to boolean
            if filters.get('is_public') is not None:
                filters['is_public'] = filters['is_public'].lower() == 'true'

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))

            result = SystemSettingService.get_list(
                filters=filters,
                page=page,
                limit=limit
            )

            paginator = self.pagination_class()
            serialized_data = SystemSettingListSerializer(
                result['data'],
                many=True,
                context={'request': request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="System settings retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="SystemSetting",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("System setting retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /settings/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["System Settings"],
        request=SystemSettingCreateSerializer,
        responses={
            201: SystemSettingCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new system setting. Admin only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new system setting."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to create system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SystemSettingCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="SystemSetting",
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
            setting = SystemSettingService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = SystemSettingReadSerializer(setting, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="System setting created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("System setting creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create system setting.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /settings/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["System Settings"],
        request=SystemSettingUpdateSerializer,
        responses={
            200: SystemSettingUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing system setting. Admin only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a system setting."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to update system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        setting = SystemSettingService.get_by_id(id)
        if not setting:
            return _error(
                data={"detail": "System setting not found."},
                message="System setting not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SystemSettingUpdateSerializer(
            setting,
            data=request.data,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = SystemSettingService.update(
                setting_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = SystemSettingReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="System setting updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("System setting update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update system setting.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /settings/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["System Settings"],
        request=SystemSettingUpdateSerializer,
        responses={
            200: SystemSettingUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing system setting. Admin only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a system setting."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to update system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        setting = SystemSettingService.get_by_id(id)
        if not setting:
            return _error(
                data={"detail": "System setting not found."},
                message="System setting not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SystemSettingUpdateSerializer(
            setting,
            data=request.data,
            partial=True,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = SystemSettingService.update(
                setting_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = SystemSettingReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="System setting updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("System setting partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update system setting.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /settings/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["System Settings"],
        responses={
            204: SystemSettingDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a system setting. Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a system setting."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to delete system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        setting = SystemSettingService.get_by_id(id)
        if not setting:
            return _error(
                data={"detail": "System setting not found."},
                message="System setting not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            SystemSettingService.delete(
                setting_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="System setting deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("System setting deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete system setting.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /settings/bulk-update/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["System Settings"],
        request=SystemSettingBulkUpdateSerializer,
        responses={
            200: SystemSettingBulkUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk create or update system settings. Admin only."
    )
    @transaction.atomic
    def post(self, request):
        """Bulk create or update system settings."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to bulk update system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SystemSettingBulkUpdateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="update_grouped_config",
                model_name="SystemSetting",
                object_id="bulk",
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
            result = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type="update_grouped_config",
                model_name="SystemSetting",
                object_id="bulk",
                changes={
                    "created": len(result['created']),
                    "updated": len(result['updated']),
                    "errors": len(result['errors']),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message=f"Bulk update completed: {len(result['created'])} created, {len(result['updated'])} updated, {len(result['errors'])} errors.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk update system settings.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# System Setting Grouped View
# ----------------------------------------------------------------------

class SystemSettingGroupedView(APIView):
    """
    Get all system settings grouped by setting_type.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["System Settings"],
        responses={
            200: SystemSettingGroupedResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get all system settings grouped by setting_type. Admin/Staff only."
    )
    def get(self, request):
        """Get grouped system settings."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to view grouped system settings."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            grouped = SystemSettingService.get_grouped()

            log_audit_event(
                request=request,
                user=user,
                action_type="read_grouped_config",
                model_name="SystemSetting",
                object_id="grouped",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=grouped,
                message="Grouped system settings retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Grouped settings error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve grouped system settings.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# System Setting Public View
# ----------------------------------------------------------------------

class SystemSettingPublicView(APIView):
    """
    Get public system settings (no authentication required).
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["System Settings"],
        responses={
            200: SystemSettingPublicResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get public system settings. No authentication required."
    )
    def get(self, request):
        """Get public system settings."""
        try:
            public_settings = SystemSettingService.get_public()

            return _success(
                data=public_settings,
                message="Public system settings retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Public settings error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve public system settings.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# System Setting System Info View
# ----------------------------------------------------------------------

class SystemSettingSystemInfoView(APIView):
    """
    Get system information.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["System Settings"],
        responses={
            200: SystemSettingSystemInfoResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get system information (version, environment, timezone). No authentication required."
    )
    def get(self, request):
        """Get system information."""
        try:
            system_info = SystemSettingService.get_system_info()

            return _success(
                data=system_info,
                message="System information retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("System info error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve system information.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )