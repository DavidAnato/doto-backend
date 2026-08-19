"""Middleware sécurité assouplie pour dév / démo (sans .env obligatoire)."""
from django.conf import settings


class OpenSecurityMiddleware:
    """Désactive les checks CSRF quand OPEN_CSRF / DEBUG / CORS ouvert.

    L'API s'authentifie surtout via JWT (Authorization) - pas de cookie session
    cross-origin requis. En prod stricte : DJANGO_DEBUG=False et OPEN_CSRF=False.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "OPEN_CSRF", False) or settings.DEBUG:
            request._dont_enforce_csrf_checks = True
        return self.get_response(request)
