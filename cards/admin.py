from django.contrib import admin

from .models import DodoCard


@admin.register(DodoCard)
class DodoCardAdmin(admin.ModelAdmin):
    list_display = (
        "patient",
        "statut",
        "date_creation",
        "date_expiration",
        "lost_at",
        "motif",
        "is_active",
    )
    list_filter = ("statut",)
    search_fields = ("patient__npi", "patient__nom", "motif")
    readonly_fields = ("token_chiffre", "date_creation", "revoquee_le", "lost_at")
