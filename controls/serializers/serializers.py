# controls/serializers.py
from rest_framework import serializers

class TaskTriggerResponseSerializer(serializers.Serializer):
    task_id = serializers.CharField(help_text="Celery task ID")
    status = serializers.CharField(default="queued", help_text="Task status")

class TaskStatusResponseSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    last_run = serializers.DictField(allow_null=True)
    is_running = serializers.BooleanField()
    schedule = serializers.CharField(allow_null=True)

class HealthCheckResponseSerializer(serializers.Serializer):
    issues_found = serializers.IntegerField()
    issues = serializers.ListField(child=serializers.DictField())

class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)