"""Routage racine de l'API DOTO+."""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

# Aucun import de vues / admin.site.urls ici : tout passe par include("…")
# en chaîne. Pendant le chargement de ce module le verrou _ModuleLock('config.urls')
# est tenu ; un reverse() ou la page 500 DEBUG (resolve) réimporterait ce fichier
# et provoquerait un deadlock (gunicorn --threads, Render).
urlpatterns = [
    path("admin/", include(("config.admin_urls", "admin"), namespace="admin")),
    path("api/", include("core.urls")),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("patients.urls")),
    path("api/", include("medical.urls")),
    path("api/", include("cards.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("notifications.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
