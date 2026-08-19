"""Consultations, ordonnances, examens, constantes vitales (CDC §3.5, §6.3)."""
from django.conf import settings
from django.db import models

from patients.models import Patient
from accounts.models import StructureSante


class Consultation(models.Model):
    """Timeline des consultations (table `consultations`, CDC §6.3).

    Patient → Consultation → Médecin + Spécialité + Structure + Type + Motif + Diagnostic + Notes
    (+ RDV lié optionnel). Formulaire unique extensible via `extra` (JSON).
    """

    class Type(models.TextChoices):
        CONSULTATION = "consultation", "Consultation"
        HOSPITALISATION = "hospitalisation", "Hospitalisation"
        URGENCE = "urgence", "Urgence"
        SUIVI = "suivi", "Suivi/Contrôle"
        CHIRURGIE = "chirurgie", "Chirurgie"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="consultations")
    structure = models.ForeignKey(StructureSante, on_delete=models.SET_NULL, null=True, blank=True)
    medecin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consultations",
    )
    appointment = models.ForeignKey(
        "patients.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    date = models.DateTimeField()
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.CONSULTATION)
    specialite = models.CharField(max_length=80, blank=True)
    motif = models.CharField(max_length=255, blank=True)
    diagnostic = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    extra = models.JSONField(default=dict, blank=True)
    annule = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Consultation"
        ordering = ["-date"]

    def __str__(self):
        spec = self.specialite or "Consultation"
        return f"{spec} - {self.patient.full_name} ({self.date:%d/%m/%Y})"


class Ordonnance(models.Model):
    """Ordonnance (table `ordonnances`, CDC §3.5, §4.5)."""

    class Statut(models.TextChoices):
        ACTIVE = "active", "Active"
        TERMINEE = "terminee", "Terminée"
        PAYEE = "payee", "Payé"
        DISPENSEE = "dispensee", "Payé"  # alias rétrocompat (ancien « Dispensé »)
        ANNULEE = "annulee", "Annulée"

    PAID_VALUES = (Statut.PAYEE, Statut.DISPENSEE, "dispense")

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
    statut = models.CharField(max_length=16, choices=Statut.choices, default=Statut.ACTIVE)
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
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ordonnance"
        ordering = ["-date"]

    def __str__(self):
        return f"Ordonnance {self.patient.full_name} - {self.date}"


class Medicament(models.Model):
    """Ligne de médicament d'une ordonnance (saisie détaillée)."""

    ordonnance = models.ForeignKey(Ordonnance, on_delete=models.CASCADE, related_name="medicaments")
    nom = models.CharField(max_length=150)
    dosage = models.CharField(max_length=80, blank=True)
    forme = models.CharField(max_length=40, blank=True)
    quantite = models.CharField(max_length=80, blank=True, help_text="Quantité à délivrer, ex. 1 boîte")
    unites_par_prise = models.CharField(max_length=40, blank=True, help_text="Ex. 2 comprimés")
    frequence_par_jour = models.CharField(max_length=40, blank=True, help_text="Ex. 3/jour")
    frequence = models.CharField(max_length=120, blank=True, help_text="Posologie combinée (rétrocompat)")
    duree_jours = models.PositiveIntegerField(null=True, blank=True)
    moment = models.CharField(max_length=40, blank=True)
    instructions = models.CharField(max_length=255, blank=True)

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
    bon = models.ForeignKey(
        "BonExamen",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resultats",
    )
    ligne = models.ForeignKey(
        "BonExamenLigne",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resultats",
    )
    annule = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Examen"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.type_examen} - {self.patient.full_name}"


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


class BonExamen(models.Model):
    """Bon de prescription d'examens (1..n lignes) - workflow labo."""

    class Statut(models.TextChoices):
        DEMANDE = "demande", "Demandé"
        RECU = "recu", "Reçu"
        EN_COURS = "en_cours", "En cours"
        RESULTAT_DISPONIBLE = "resultat_disponible", "Résultat disponible"
        CLOTURE = "cloture", "Clôturé"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="bons_examen")
    medecin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bons_examen_prescrits",
    )
    structure = models.ForeignKey(
        StructureSante,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bons_examen_origine",
        help_text="Structure du médecin prescripteur.",
    )
    laboratoire = models.ForeignKey(
        StructureSante,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bons_examen_labo",
        help_text="Laboratoire / structure destinataire.",
    )
    laboratoire_nom = models.CharField(max_length=150, blank=True)
    motif = models.CharField(max_length=255, blank=True)
    observations = models.TextField(blank=True)
    statut = models.CharField(max_length=24, choices=Statut.choices, default=Statut.DEMANDE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bon d'examen"
        verbose_name_plural = "Bons d'examen"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Bon #{self.pk} - {self.patient.full_name} ({self.get_statut_display()})"

    def refresh_statut_from_lignes(self, save=True):
        """Passe en résultat disponible si toutes les lignes ont un résultat."""
        lignes = list(self.lignes.all())
        if not lignes:
            return
        if all(l.resultats.filter(annule=False).exists() for l in lignes):
            if self.statut not in (self.Statut.CLOTURE,):
                self.statut = self.Statut.RESULTAT_DISPONIBLE
                if save:
                    self.save(update_fields=["statut", "updated_at"])


class BonExamenLigne(models.Model):
    """Examen prescrit sur un bon."""

    bon = models.ForeignKey(BonExamen, on_delete=models.CASCADE, related_name="lignes")
    type_examen = models.CharField(max_length=120)
    categorie = models.CharField(
        max_length=12,
        choices=Examen.Categorie.choices,
        default=Examen.Categorie.ANALYSES,
    )
    code = models.CharField(max_length=40, blank=True)

    def __str__(self):
        return self.type_examen
