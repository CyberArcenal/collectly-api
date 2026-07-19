# controls/views/system_setting.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from system_settings.tasks.system_setting import (
    force_settings_cache_refresh,
    force_settings_validate,
    force_settings_backup,
    check_settings_diff,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerSettingsCacheRefreshView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Refresh settings cache",
        description="Pre-load all system settings into cache.",
        request=inline_serializer(
            name="SettingsCacheRefreshRequest",
            fields={
                "setting_type": serializers.CharField(
                    required=False,
                    help_text="Optional filter by setting type"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SettingsCacheRefreshResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            setting_type = request.data.get('setting_type')
            task = force_settings_cache_refresh.delay(setting_type=setting_type)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_settings_cache_refresh',
                model_name='Controls',
                object_id='settings_cache',
                changes={'task_id': task.id, 'setting_type': setting_type},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Settings cache refresh task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger settings cache refresh")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerSettingsValidateView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Validate system settings",
        description="Check system settings for consistency and required values.",
        request=inline_serializer(
            name="SettingsValidateRequest",
            fields={
                "setting_type": serializers.CharField(
                    required=False,
                    help_text="Optional filter by setting type"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SettingsValidateResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            setting_type = request.data.get('setting_type')
            task = force_settings_validate.delay(setting_type=setting_type)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_settings_validate',
                model_name='Controls',
                object_id='settings_validate',
                changes={'task_id': task.id, 'setting_type': setting_type},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Settings validation task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger settings validation")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerSettingsBackupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Backup system settings",
        description="Export all system settings to a JSON backup file.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SettingsBackupResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            task = force_settings_backup.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_settings_backup',
                model_name='Controls',
                object_id='settings_backup',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Settings backup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger settings backup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerSettingsDiffView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Check settings differences",
        description="Compare current settings with the latest backup.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="SettingsDiffResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": serializers.DictField(),
                    }
                ),
                description="Diff check completed.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = check_settings_diff()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_settings_diff',
                model_name='Controls',
                object_id='settings_diff',
                changes={'differences': result.get('total_differences', 0)},
                ip_address=get_client_ip(request),
            )
            return _success(
                data=result,
                message='Settings diff check completed.',
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to check settings diff")
            return _error(
                data={"detail": str(e)},
                message="Failed to check diff.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )