"""Notifications in-app + tokens push (Expo / web)."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """Notification in-app destinée à un utilisateur (patient, pro ou admin)."""

    class Type(models.TextChoices):
        ACCESS_REQUEST = "access_request", "Demande d'accès"
        ACCESS_GRANTED = "access_granted", "Accès autorisé"
        ACCESS_DENIED = "access_denied", "Accès refusé"
        ACCESS_EXPIRED = "access_expired", "Demande expirée"
        DOSSIER_UPDATED = "dossier_updated", "Dossier mis à jour"
        ORDONNANCE = "ordonnance", "Ordonnance"
        EXAMEN = "examen", "Examen"
        SYSTEM = "system", "Système"
        EMERGENCY = "emergency", "Urgence"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    type = models.CharField(max_length=40, choices=Type.choices, default=Type.SYSTEM)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return f"{self.user_id}: {self.title}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])


class DeviceToken(models.Model):
    """Jeton push Expo (mobile) ou web push stub."""

    class Platform(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"
        WEB = "web", "Web"
        UNKNOWN = "unknown", "Inconnu"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=512)
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.UNKNOWN)
    app = models.CharField(
        max_length=40,
        blank=True,
        help_text="dotoplus | dotohub-mobile | dotohub | dotoplus-admin",
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("user", "token")]
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user_id} · {self.platform} · {self.token[:16]}…"
