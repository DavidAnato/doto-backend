from django.contrib import admin

from .models import AccessBlock, AccessRequest, Appointment, Assurance, DossierMedical, Patient


class DossierInline(admin.StackedInline):
    model = DossierMedical
    extra = 0


class AssuranceInline(admin.StackedInline):
    model = Assurance
    extra = 0


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("npi", "nom", "prenom", "groupe_sanguin", "electrophorese", "npi_verifie_anip")
    list_filter = ("groupe_sanguin", "sexe", "npi_verifie_anip")
    search_fields = ("npi", "nom", "prenom", "telephone", "electrophorese")
    fields = (
        "user", "npi", "npi_verifie_anip", "nom", "prenom", "date_naissance", "lieu_naissance", "sexe",
        "nom_pere", "nom_mere", "adresse_commune", "adresse_quartier",
        "groupe_sanguin", "electrophorese", "photo", "telephone", "email",
        "contact_urgence_nom", "contact_urgence_lien", "tel_urgence",
    )
    inlines = [DossierInline, AssuranceInline]


@admin.register(Assurance)
class AssuranceAdmin(admin.ModelAdmin):
    list_display = ("assureur", "num_police", "droits_valides", "valide_au")
    search_fields = ("assureur", "num_police")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "requester", "status", "mode", "created_at", "expires_at")
    list_filter = ("status", "mode")
    search_fields = ("patient__npi", "patient__nom", "requester__username")
    readonly_fields = ("created_at", "responded_at")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "structure", "professionnel", "debut", "statut")
    list_filter = ("statut",)
    search_fields = ("patient__npi", "patient__nom", "motif")


@admin.register(AccessBlock)
class AccessBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "blocked_user", "blocked_structure", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("patient__npi", "reason")
