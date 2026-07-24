"""Routage racine de l'API DOTO+."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import health

admin.site.site_header = "DOTO+ Admin"
admin.site.site_title = "DOTO+"
admin.site.index_title = "Administration de la plateforme"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("patients.urls")),
    path("api/", include("medical.urls")),
    path("api/", include("cards.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("notifications.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
