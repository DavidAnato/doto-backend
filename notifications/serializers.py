from rest_framework import serializers

from .models import DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "title",
            "body",
            "type",
            "payload",
            "read_at",
            "is_read",
            "created_at",
        )
        read_only_fields = fields


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ("id", "token", "platform", "app", "enabled", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
