from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AffiliationPro, KycDossier, StructureSante, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "role", "actif", "is_locked", "has_photo")
    list_filter = ("role", "actif", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "DOTO+",
            {
                "fields": (
                    "role",
                    "telephone",
                    "photo",
                    "specialite",
                    "type_exercice",
                    "ville_exercice",
                    "nom_etablissement",
                    "numero_autorisation",
                    "numero_ordre",
                    "email_pro",
                    "ligne_pro",
                    "structures",
                    "structure_principale",
                    "actif",
                )
            },
        ),
    )

    @admin.display(boolean=True, description="Photo")
    def has_photo(self, obj):
        return bool(obj.photo)


@admin.register(AffiliationPro)
class AffiliationProAdmin(admin.ModelAdmin):
    list_display = ("user", "nom_etablissement", "kind", "ville", "principal", "statut")
    list_filter = ("kind", "statut", "principal")
    search_fields = ("user__username", "nom_etablissement", "numero_ordre")


@admin.register(KycDossier)
class KycDossierAdmin(admin.ModelAdmin):
    list_display = ("user", "subject", "statut", "nom", "prenom", "npi", "submitted_at")
    list_filter = ("statut", "subject")
    search_fields = ("nom", "prenom", "npi", "user__username")


@admin.register(StructureSante)
class StructureSanteAdmin(admin.ModelAdmin):
    list_display = ("nom", "type", "department", "commune", "localisation", "code_structure", "statut_partenaire")
    list_filter = ("type", "statut_partenaire", "department", "ownership")
    search_fields = ("nom", "full_name", "code_structure", "commune")
