from django.contrib import admin

from .models import Consultation, ConstanteVitale, Examen, Medicament, Ordonnance


class MedicamentInline(admin.TabularInline):
    model = Medicament
    extra = 1


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("patient", "date", "type", "diagnostic", "medecin")
    list_filter = ("type",)
    search_fields = ("patient__npi", "patient__nom", "diagnostic")


@admin.register(Ordonnance)
class OrdonnanceAdmin(admin.ModelAdmin):
    list_display = ("patient", "date", "statut", "medecin")
    list_filter = ("statut",)
    inlines = [MedicamentInline]


@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ("patient", "type_examen", "categorie", "statut", "date")
    list_filter = ("categorie", "statut")


@admin.register(ConstanteVitale)
class ConstanteVitaleAdmin(admin.ModelAdmin):
    list_display = ("patient", "date", "tension_systolique", "temperature", "glycemie")
