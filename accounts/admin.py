from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import StructureSante, User


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


@admin.register(StructureSante)
class StructureSanteAdmin(admin.ModelAdmin):
    list_display = ("nom", "type", "localisation", "code_structure", "statut_partenaire")
    list_filter = ("type", "statut_partenaire")
    search_fields = ("nom", "code_structure")
