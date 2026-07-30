"""DotoCard — carte d'accès QR (table `tokens_qr`, CDC §2)."""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from patients.models import Patient

from . import services


class DodoCard(models.Model):
    """
    Carte d'accès physique DotoCard reliée à un patient.

    Le token chiffré est la seule donnée présente dans le QR. En cas de
    perte, le token est révoqué côté serveur (< 1 min) sans compromettre
    le dossier (CDC §2.4, §6.2).
    """

    class Statut(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOQUEE = "revoquee", "Révoquée (perte/vol)"
        EXPIREE = "expiree", "Expirée"
        REEMISE = "reemise", "Réémise"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="dodocards")
    token_chiffre = models.TextField(unique=True)
    cvv = models.CharField(max_length=3, blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ACTIVE)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateField()
    revoquee_le = models.DateTimeField(null=True, blank=True)
    revoquee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dodocards_revoquees",
    )
    lost_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Horodatage du signalement de perte/vol.",
    )
    motif = models.CharField(
        max_length=160,
        blank=True,
        help_text="Motif de révocation / réémission (ex. perte, vol, demande).",
    )

    class Meta:
        verbose_name = "DotoCard"
        verbose_name_plural = "DotoCards"
        ordering = ["-date_creation"]

    def __str__(self):
        return f"DotoCard {self.patient.npi} — {self.get_statut_display()}"

    @property
    def is_active(self):
        return (
            self.statut == self.Statut.ACTIVE
            and self.date_expiration >= timezone.now().date()
        )

    @classmethod
    def issue(cls, patient, validity_years=5, cvv=""):
        """Émet une nouvelle DotoCard (validité 5 ans, CDC §2.1)."""
        token = services.generate_token(patient.npi)
        return cls.objects.create(
            patient=patient,
            token_chiffre=token,
            cvv=cvv or "000",
            date_expiration=timezone.now().date() + timedelta(days=365 * validity_years),
        )

    def revoke(self, user=None, motif: str = "", mark_lost: bool = False):
        """Invalide le token (perte/vol) — dossier intact."""
        now = timezone.now()
        self.statut = self.Statut.REVOQUEE
        self.revoquee_le = now
        self.revoquee_par = user
        updates = ["statut", "revoquee_le", "revoquee_par"]
        if motif:
            self.motif = motif[:160]
            updates.append("motif")
        if mark_lost or (motif and "perte" in motif.lower()) or (motif and "vol" in motif.lower()):
            self.lost_at = now
            updates.append("lost_at")
        self.save(update_fields=updates)

    def mark_reissued(self, user=None, motif: str = ""):
        """Passe la carte à l'état réémise (après remplacement)."""
        now = timezone.now()
        if self.statut == self.Statut.ACTIVE:
            self.revoquee_le = now
            self.revoquee_par = user
        self.statut = self.Statut.REEMISE
        if motif:
            self.motif = motif[:160]
        self.save(update_fields=["statut", "revoquee_le", "revoquee_par", "motif"])

    @classmethod
    def replace(cls, old: "DodoCard", user=None, motif: str = "reemission", mark_lost: bool = False):
        """Révoque/marque l'ancienne carte et émet un nouveau token (< 1 min)."""
        if old.statut == cls.Statut.ACTIVE:
            if mark_lost:
                old.revoke(user=user, motif=motif or "perte", mark_lost=True)
            else:
                old.revoke(user=user, motif=motif or "reemission")
        old.mark_reissued(user=user, motif=motif or old.motif or "reemission")
        return cls.issue(old.patient, cvv=old.cvv)
