import csv

from django.http import HttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action

from core.permissions import IsAdmin

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Consultation du journal d'audit - admin uniquement. Export CSV (CDC §3.5)."""

    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdmin]
    search_fields = ["username", "action", "target", "patient_npi", "ip"]
    filterset_fields = ["action", "username", "method"]

    @action(detail=False, methods=["get"])
    def export(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="audit_doto.csv"'
        writer = csv.writer(response)
        writer.writerow(["timestamp", "utilisateur", "action", "cible", "npi", "ip", "methode", "chemin"])
        for log in self.filter_queryset(self.get_queryset()):
            writer.writerow([
                log.timestamp, log.username, log.action, log.target,
                log.patient_npi, log.ip, log.method, log.path,
            ])
        return response
