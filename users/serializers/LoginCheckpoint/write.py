# users/serializers/LoginCheckpoint/write.py
import uuid
from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth import get_user_model

from users.models.login_checkpoint import LoginCheckpoint

User = get_user_model()


class LoginCheckpointWriteSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True
    )

    class Meta:
        model = LoginCheckpoint
        fields = ["user"]
        extra_kwargs = {
            'token': {'read_only': True},
        }

    def create(self, validated_data):
        validated_data['token'] = uuid.uuid4().hex
        validated_data['expires_at'] = timezone.now() + timezone.timedelta(minutes=15)
        return LoginCheckpoint.objects.create(**validated_data)