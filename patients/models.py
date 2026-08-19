"""Patients, dossiers médicaux et assurance (CDC §2, §3.4, §4, §6.3)."""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Patient(models.Model):
    """Titulaire de la carte DotoCard (table `patients`, CDC §6.3)."""

    class GroupeSanguin(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"
        NON_IDENTIFIE = "Non identifié", "Non identifié"

    class Electrophorese(models.TextChoices):
        AA = "AA", "AA"
        AS = "AS", "AS"
        SS = "SS", "SS"
        AC = "AC", "AC"
        SC = "SC", "SC"
        CC = "CC", "CC"
        NON_IDENTIFIE = "Non identifié", "Non identifié"

    # Compte optionnel pour l'app mobile DotoPlus (rôle patient).
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patient",
    )

    npi = models.CharField(
        "NPI (ANIP)", max_length=30, unique=True,
        help_text="Numéro Personnel d'Identification officiel, format BJ-XXXX-XXXXXXXX.",
    )
    npi_verifie_anip = models.BooleanField("Vérifié ANIP", default=False)

    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    date_naissance = models.DateField(null=True, blank=True)
    lieu_naissance = models.CharField("Lieu de naissance", max_length=120, blank=True)
    sexe = models.CharField(
        max_length=1, choices=[("M", "Masculin"), ("F", "Féminin")], blank=True
    )
    # Verso DotoCard - filiation & adresse de résidence
    nom_pere = models.CharField("Nom du père", max_length=120, blank=True)
    nom_mere = models.CharField("Nom de la mère", max_length=120, blank=True)
    adresse_commune = models.CharField("Commune", max_length=120, blank=True)
    adresse_quartier = models.CharField("Quartier", max_length=120, blank=True)
    groupe_sanguin = models.CharField(
        max_length=20, choices=GroupeSanguin.choices, blank=True
    )
    # Électrophorèse de l'hémoglobine - choix connus ou texte libre (ex. « Non identifié »).
    electrophorese = models.CharField(
        "Électrophorèse Hb",
        max_length=40,
        blank=True,
        help_text="Phénotype Hb (AA, AS, SS…) ou Non identifié / texte libre.",
    )
    photo = models.ImageField(upload_to="patients/photos/", null=True, blank=True)

    telephone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)

    # PIN 4 chiffres pour déverrouillage app (optionnel) - hashé (jamais en clair).
    pin_hash = models.CharField(max_length=128, blank=True)
    failed_pin_attempts = models.PositiveIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)

    # Verrouillage session (paramètres patient - sync via PATCH me)
    require_unlock = models.BooleanField(
        "Exiger déverrouillage",
        default=False,
        help_text="Si vrai : PIN ou biométrie à chaque ouverture de l'app.",
    )
    urgence_when_locked = models.BooleanField(
        "Urgence si verrouillé",
        default=True,
        help_text="Autorise l'accès au mode Urgence depuis l'écran de verrouillage.",
    )

    # Contact d'urgence (mode urgence, CDC §4.9)
    contact_urgence_nom = models.CharField(max_length=120, blank=True)
    contact_urgence_lien = models.CharField(max_length=60, blank=True)
    tel_urgence = models.CharField(max_length=30, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Patient"
        verbose_name_plural = "Patients"
        ordering = ["nom", "prenom"]

    def __str__(self):
        return f"{self.nom} {self.prenom} - {self.npi}"

    @property
    def full_name(self):
        return f"{self.nom} {self.prenom}".strip()

    @property
    def has_pin(self):
        return bool(self.pin_hash)

    def set_pin(self, raw_pin: str):
        from django.contrib.auth.hashers import make_password

        self.pin_hash = make_password(raw_pin)
        self.failed_pin_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=["pin_hash", "failed_pin_attempts", "pin_locked_until"])

    def check_pin(self, raw_pin: str) -> bool:
        from django.contrib.auth.hashers import check_password

        if not self.pin_hash:
            return False
        return check_password(raw_pin, self.pin_hash)


class DossierMedical(models.Model):
    """Dossier médical centralisé (table `dossiers_medicaux`, CDC §6.3)."""

    patient = models.OneToOneField(
        Patient, on_delete=models.CASCADE, related_name="dossier"
    )
    antecedents = models.TextField(blank=True)
    # Allergies critiques (pills rouges) - liste de libellés.
    allergies = models.JSONField(default=list, blank=True)
    # Maladies chroniques (pills ambrées) : [{"nom": "...", "depuis": "2019"}]
    maladies_chroniques = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dossier médical"
        verbose_name_plural = "Dossiers médicaux"

    def __str__(self):
        return f"Dossier - {self.patient.full_name}"


class Assurance(models.Model):
    """Couverture assurantielle (table `assurances`, CDC §2.3, §4.7)."""

    patient = models.OneToOneField(
        Patient, on_delete=models.CASCADE, related_name="assurance"
    )
    assureur = models.CharField(max_length=120)
    num_police = models.CharField(max_length=80)
    type_couverture = models.CharField(max_length=120, blank=True)
    valide_du = models.DateField(null=True, blank=True)
    valide_au = models.DateField(null=True, blank=True)
    droits_valides = models.BooleanField(default=True)
    # 6 catégories : [{"categorie","taux","plafond"}]
    garanties = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Assurance"
        verbose_name_plural = "Assurances"

    def __str__(self):
        return f"{self.assureur} - {self.num_police}"

    @staticmethod
    def garanties_par_defaut():
        """Barème des garanties issu du cahier des charges (CDC §2.3)."""
        return [
            {"categorie": "Consultation générale", "taux": 80, "plafond": 150000},
            {"categorie": "Consultation spécialisée", "taux": 70, "plafond": 200000},
            {"categorie": "Médicaments (liste)", "taux": 80, "plafond": 100000},
            {"categorie": "Hospitalisation", "taux": 90, "plafond": 500000},
            {"categorie": "Examens & imagerie", "taux": 75, "plafond": 180000},
            {"categorie": "Maternité", "taux": 100, "plafond": 300000},
        ]


class AccessRequest(models.Model):
    """Demande d'accès dossier - consentement patient (sauf urgence)."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvé"
        DENIED = "denied", "Refusé"
        EXPIRED = "expired", "Expiré"
        EMERGENCY_BYPASS = "emergency_bypass", "Urgence (bypass)"
        REVOKED = "revoked", "Révoqué"
        CANCELLED = "cancelled", "Annulé (pro)"

    class Mode(models.TextChoices):
        SCAN = "scan", "Scan DotoCard"
        SEARCH = "search", "Recherche / ouverture"
        EMERGENCY = "emergency", "Mode urgence"

    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="access_requests"
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_requests_made",
    )
    structure = models.ForeignKey(
        "accounts.StructureSante",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_requests",
    )
    dodocard = models.ForeignKey(
        "cards.DodoCard",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_requests",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.SCAN)
    reason = models.CharField(max_length=255, blank=True)
    scope = models.CharField(max_length=40, default="full")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    grant_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["requester", "status"]),
        ]

    def __str__(self):
        return f"AccessRequest#{self.pk} {self.status} patient={self.patient_id}"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING and timezone.now() < self.expires_at

    @property
    def has_active_grant(self) -> bool:
        if self.status == self.Status.EMERGENCY_BYPASS:
            if self.grant_expires_at and timezone.now() > self.grant_expires_at:
                return False
            return True
        if self.status != self.Status.APPROVED:
            return False
        if self.grant_expires_at and timezone.now() > self.grant_expires_at:
            return False
        return True


def default_request_expiry():
    minutes = getattr(settings, "ACCESS_REQUEST_TTL_MINUTES", 2)
    return timezone.now() + timedelta(minutes=minutes)


def default_grant_expiry(emergency: bool = False):
    if emergency:
        minutes = getattr(settings, "ACCESS_EMERGENCY_GRANT_TTL_MINUTES", 30)
    else:
        minutes = getattr(settings, "ACCESS_GRANT_TTL_MINUTES", 60)
    return timezone.now() + timedelta(minutes=minutes)


class Appointment(models.Model):
    """Rendez-vous patient ↔ pro / structure (CDC parcours patient + réception)."""

    class Statut(models.TextChoices):
        PLANIFIE = "planifie", "Planifié"
        CONFIRME = "confirme", "Confirmé"
        ANNULE = "annule", "Annulé"
        TERMINE = "termine", "Terminé"
        ABSENT = "absent", "Absent"

    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="appointments"
    )
    structure = models.ForeignKey(
        "accounts.StructureSante",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments",
    )
    professionnel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments_created",
    )
    debut = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    motif = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    statut = models.CharField(
        max_length=12, choices=Statut.choices, default=Statut.PLANIFIE, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rendez-vous"
        verbose_name_plural = "Rendez-vous"
        ordering = ["debut"]
        indexes = [
            models.Index(fields=["patient", "debut"]),
            models.Index(fields=["professionnel", "debut"]),
            models.Index(fields=["structure", "debut"]),
        ]

    def __str__(self):
        return f"RDV {self.patient.full_name} - {self.debut:%d/%m/%Y %H:%M}"


class AccessBlock(models.Model):
    """
    Blocage permanent (blacklist) d'un professionnel et/ou d'une structure
    par le patient - empêche toute nouvelle demande d'accès.
    """

    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="access_blocks"
    )
    blocked_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="access_blocks_received",
    )
    blocked_structure = models.ForeignKey(
        "accounts.StructureSante",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="access_blocks",
    )
    reason = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    lifted_at = models.DateTimeField(null=True, blank=True)
    created_by_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_blocks_forced",
        help_text="Si renseigné : blocage forcé par un admin.",
    )

    class Meta:
        verbose_name = "Blocage d'accès"
        verbose_name_plural = "Blocages d'accès"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(blocked_user__isnull=False)
                    | models.Q(blocked_structure__isnull=False)
                ),
                name="accessblock_target_required",
            ),
        ]

    def __str__(self):
        target = (
            self.blocked_user.get_full_name()
            if self.blocked_user
            else (self.blocked_structure.nom if self.blocked_structure else "?")
        )
        return f"Block {self.patient.full_name} → {target}"
