"""Comptes utilisateurs, structures de santé et RBAC (CDC §1.3, §3, §6.3)."""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class StructureSante(models.Model):
    """Structure de santé partenaire (table `structures_sante`, CDC §6.3)."""

    class Type(models.TextChoices):
        HOPITAL = "hopital", "Hôpital"
        CLINIQUE = "clinique", "Clinique"
        POLYCLINIQUE = "polyclinique", "Polyclinique"
        CENTRE = "centre", "Centre de santé"
        PHARMACIE = "pharmacie", "Pharmacie"
        LABORATOIRE = "laboratoire", "Laboratoire"
        INDEPENDANT = "independant", "Indépendant"

    nom = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.CLINIQUE)
    localisation = models.CharField(max_length=200, blank=True)
    code_structure = models.CharField(
        max_length=20, unique=True,
        help_text="Code utilisé dans le double facteur mobile (loi 2017-20).",
    )
    statut_partenaire = models.BooleanField(default=True)
    telephone = models.CharField(max_length=30, blank=True)
    # Catalogue hôpitaux Bénin
    catalog_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    ownership = models.CharField(max_length=40, blank=True)
    department = models.CharField(max_length=80, blank=True)
    commune = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Structure de santé"
        verbose_name_plural = "Structures de santé"
        ordering = ["nom"]

    def __str__(self):
        return f"{self.nom} ({self.get_type_display()})"


class User(AbstractUser):
    """
    Utilisateur unifié : professionnels de santé et administrateurs.

    Les patients disposent d'un profil dédié (`patients.Patient`) relié
    optionnellement à un compte pour l'application mobile DotoPlus.
    """

    class Role(models.TextChoices):
        PATIENT = "patient", "Patient"
        MEDECIN = "medecin", "Médecin"
        INFIRMIER = "infirmier", "Infirmier"
        PHARMACIEN = "pharmacien", "Pharmacien"
        LABORANTIN = "laborantin", "Laborantin"
        AMBULANCIER = "ambulancier", "Ambulancier"
        RECEPTIONNISTE = "receptionniste", "Réceptionniste"
        ADMIN = "admin", "Admin structure"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEDECIN)
    telephone = models.CharField(max_length=30, blank=True)
    # Photo d'identité (visage centré) - obligatoire pour profil complet.
    photo = models.ImageField(
        upload_to="users/photos/",
        null=True,
        blank=True,
        help_text="Photo d'identité (visage centré).",
    )
    # Un professionnel peut être rattaché à plusieurs structures (CDC §3.6).
    structures = models.ManyToManyField(
        StructureSante, related_name="professionnels", blank=True
    )
    structure_principale = models.ForeignKey(
        StructureSante,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agents_principaux",
    )
    actif = models.BooleanField(default=True)

    # Sécurité connexion (CDC §3.2)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class TypeExercice(models.TextChoices):
        ETABLISSEMENT = "etablissement_sante", "Établissement de santé"
        PHARMACIE = "pharmacie", "Pharmacie"
        LABORATOIRE = "laboratoire", "Laboratoire"
        INDEPENDANT = "independant", "Indépendant"

    # Spécialité principale (médecins) - préremplit la consultation, modifiable.
    specialite = models.CharField(max_length=80, blank=True, default="Médecine générale")
    type_exercice = models.CharField(
        max_length=32, choices=TypeExercice.choices, blank=True
    )
    ville_exercice = models.CharField(max_length=120, blank=True)
    nom_etablissement = models.CharField(max_length=200, blank=True)
    numero_autorisation = models.CharField(max_length=80, blank=True)
    numero_ordre = models.CharField(max_length=80, blank=True)
    email_pro = models.EmailField(blank=True)
    ligne_pro = models.CharField(max_length=30, blank=True)

    # PIN 4 chiffres - verrouillage appareil/session (obligatoire pour les pros)
    pin_hash = models.CharField(max_length=128, blank=True)
    failed_pin_attempts = models.PositiveIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self):
        return f"{self.get_full_name() or self.username} - {self.get_role_display()}"

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

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

    def clear_pin(self):
        self.pin_hash = ""
        self.failed_pin_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=["pin_hash", "failed_pin_attempts", "pin_locked_until"])

    def register_failed_login(self, max_attempts, lockout_minutes):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = timezone.now() + timezone.timedelta(minutes=lockout_minutes)
            self.failed_login_attempts = 0
        self.save(update_fields=["failed_login_attempts", "locked_until"])

    def reset_login_state(self):
        self.failed_login_attempts = 0
        self.locked_until = None
        self.save(update_fields=["failed_login_attempts", "locked_until"])


class AffiliationPro(models.Model):
    """Rattachement d'un professionnel à un établissement (catalogue ou libre)."""

    class Kind(models.TextChoices):
        ETABLISSEMENT = "etablissement_sante", "Établissement de santé"
        PHARMACIE = "pharmacie", "Pharmacie"
        LABORATOIRE = "laboratoire", "Laboratoire"
        INDEPENDANT = "independant", "Indépendant"

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        EN_ATTENTE = "en_attente", "En attente de validation"
        VALIDE = "valide", "Validé"
        REFUSE = "refuse", "Refusé"

    user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="affiliations",
    )
    structure = models.ForeignKey(
        StructureSante,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="affiliations",
    )
    nom_etablissement = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.ETABLISSEMENT)
    ville = models.CharField(max_length=120, blank=True)
    numero_autorisation = models.CharField(max_length=80, blank=True)
    numero_ordre = models.CharField(max_length=80, blank=True)
    email_pro = models.EmailField(blank=True)
    ligne_pro = models.CharField(max_length=30, blank=True)
    principal = models.BooleanField(default=False)
    statut = models.CharField(
        max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE, db_index=True
    )
    motif_refus = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Affiliation professionnelle"
        verbose_name_plural = "Affiliations professionnelles"
        ordering = ["-principal", "-created_at"]

    def __str__(self):
        nom = self.nom_etablissement or (self.structure.nom if self.structure else "Établissement")
        return f"{self.user_id} - {nom}"


class KycDossier(models.Model):
    """KYC patient ou professionnel : pièces, selfie, infos, validation admin."""

    class Subject(models.TextChoices):
        PATIENT = "patient", "Patient"
        PROFESSIONNEL = "professionnel", "Professionnel"

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        EN_ATTENTE = "en_attente", "En attente de validation"
        VALIDE = "valide", "Validé"
        REFUSE = "refuse", "Refusé"

    user = models.OneToOneField(
        "User", on_delete=models.CASCADE, related_name="kyc"
    )
    subject = models.CharField(max_length=20, choices=Subject.choices, default=Subject.PATIENT)
    statut = models.CharField(
        max_length=20, choices=Statut.choices, default=Statut.BROUILLON, db_index=True
    )
    motif_refus = models.TextField(blank=True)
    piece_recto = models.ImageField(upload_to="kyc/recto/", null=True, blank=True)
    piece_verso = models.ImageField(upload_to="kyc/verso/", null=True, blank=True)
    selfie = models.ImageField(upload_to="kyc/selfie/", null=True, blank=True)
    nom = models.CharField(max_length=120, blank=True)
    prenom = models.CharField(max_length=120, blank=True)
    date_naissance = models.DateField(null=True, blank=True)
    lieu_naissance = models.CharField(max_length=120, blank=True)
    npi = models.CharField(max_length=30, blank=True)
    telephone = models.CharField(max_length=30, blank=True)
    sexe = models.CharField(max_length=1, blank=True)
    ocr_payload = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kyc_reviews",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dossier KYC"
        verbose_name_plural = "Dossiers KYC"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"KYC {self.user_id} ({self.get_statut_display()})"
