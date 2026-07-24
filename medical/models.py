"""Consultations, ordonnances, examens, constantes vitales (CDC §3.5, §6.3)."""
from django.conf import settings
from django.db import models

from patients.models import Patient
from accounts.models import StructureSante


class Consultation(models.Model):
    """Timeline des consultations (table `consultations`, CDC §6.3)."""

    class Type(models.TextChoices):
        CONSULTATION = "consultation", "Consultation"
        HOSPITALISATION = "hospitalisation", "Hospitalisation"
        CHIRURGIE = "chirurgie", "Chirurgie"
        URGENCE = "urgence", "Urgence"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="consultations")
    structure = models.ForeignKey(StructureSante, on_delete=models.SET_NULL, null=True, blank=True)
    medecin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consultations",
    )
    date = models.DateTimeField()
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.CONSULTATION)
    diagnostic = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    annule = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Consultation"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.patient.full_name} — {self.diagnostic} ({self.date:%d/%m/%Y})"


class Ordonnance(models.Model):
    """Ordonnance (table `ordonnances`, CDC §3.5, §4.5)."""

    class Statut(models.TextChoices):
        ACTIVE = "active", "Active"
        TERMINEE = "terminee", "Terminée"
        DISPENSEE = "dispensee", "Dispensée"
        ANNULEE = "annulee", "Annulée"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="ordonnances")
    medecin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ordonnances",
    )
    structure = models.ForeignKey(StructureSante, on_delete=models.SET_NULL, null=True, blank=True)
    consultation = models.ForeignKey(
        Consultation, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordonnances"
    )
    date = models.DateField()
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.ACTIVE)
    instructions = models.TextField(blank=True)
    signature_electronique = models.CharField(max_length=255, blank=True)
    # Interactions médicamenteuses détectées (CDC §3.5)
    alertes_interactions = models.JSONField(default=list, blank=True)
    dispensee_le = models.DateTimeField(null=True, blank=True)
    dispensee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ordonnances_dispensees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ordonnance"
        ordering = ["-date"]

    def __str__(self):
        return f"Ordonnance {self.patient.full_name} — {self.date}"


class Medicament(models.Model):
    """Ligne de médicament d'une ordonnance."""

    ordonnance = models.ForeignKey(Ordonnance, on_delete=models.CASCADE, related_name="medicaments")
    nom = models.CharField(max_length=150)
    dosage = models.CharField(max_length=80, blank=True)
    frequence = models.CharField(max_length=120, blank=True)
    duree_jours = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.nom} {self.dosage}"


class Examen(models.Model):
    """Résultat d'examen uploadé par le laborantin (table `examens`, CDC §3.5, §4.6)."""

    class Categorie(models.TextChoices):
        ANALYSES = "analyses", "Analyses"
        IMAGERIE = "imagerie", "Imagerie"
        AUTRES = "autres", "Autres"

    class Statut(models.TextChoices):
        NORMAL = "normal", "Normal"
        ELEVE = "eleve", "Élevé"
        CRITIQUE = "critique", "Critique"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="examens")
    categorie = models.CharField(max_length=12, choices=Categorie.choices, default=Categorie.ANALYSES)
    type_examen = models.CharField(max_length=120)
    laboratoire = models.CharField(max_length=150, blank=True)
    laborantin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="examens_uploades",
    )
    medecin_prescripteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="examens_prescrits",
    )
    date = models.DateField()
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.NORMAL)
    resultat_texte = models.TextField(blank=True)
    commentaire_labo = models.TextField(blank=True)
    fichier = models.FileField(upload_to="examens/", null=True, blank=True)
    annule = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Examen"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.type_examen} — {self.patient.full_name}"


class ConstanteVitale(models.Model):
    """Constantes vitales saisies par l'infirmier (table `constantes_vitales`, CDC §3.5)."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="constantes")
    infirmier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="constantes_saisies",
    )
    tension_systolique = models.PositiveIntegerField(null=True, blank=True)
    tension_diastolique = models.PositiveIntegerField(null=True, blank=True)
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    poids = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    glycemie = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Constante vitale"
        ordering = ["-date"]

    def __str__(self):
        return f"Constantes {self.patient.full_name} ({self.date:%d/%m/%Y})"
