from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id", "username", "action", "target", "patient_npi",
            "ip", "user_agent", "method", "path", "timestamp",
        ]
