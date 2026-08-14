from django.contrib import admin

from .models import BonExamen, Consultation, ConstanteVitale, Examen, Medicament, Ordonnance


class MedicamentInline(admin.TabularInline):
    model = Medicament
    extra = 1


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("patient", "date", "type", "specialite", "diagnostic", "medecin", "structure")
    list_filter = ("type", "specialite")
    search_fields = ("patient__npi", "patient__nom", "diagnostic", "specialite")


@admin.register(Ordonnance)
class OrdonnanceAdmin(admin.ModelAdmin):
    list_display = ("patient", "date", "statut", "medecin")
    list_filter = ("statut",)
    inlines = [MedicamentInline]


@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ("patient", "type_examen", "categorie", "statut", "date", "bon")
    list_filter = ("categorie", "statut")


@admin.register(BonExamen)
class BonExamenAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "statut", "medecin", "laboratoire_nom", "created_at")
    list_filter = ("statut",)


@admin.register(ConstanteVitale)
class ConstanteVitaleAdmin(admin.ModelAdmin):
    list_display = ("patient", "date", "tension_systolique", "temperature", "glycemie")
