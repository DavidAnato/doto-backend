"""Journal d'audit non modifiable (CDC §3.5 Admin, §6.2 loi 2017-20)."""
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Trace : qui · quoi · quand · IP · appareil · patient concerné."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=255, blank=True)
    patient_npi = models.CharField(max_length=30, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=300, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Entrée d'audit"
        verbose_name_plural = "Journal d'audit"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.username} — {self.action}"
