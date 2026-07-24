"""Journalise automatiquement les écritures sur les endpoints sensibles."""
from .utils import client_ip

TRACKED_PREFIXES = ("/api/patients", "/api/consultations", "/api/ordonnances",
                    "/api/examens", "/api/constantes", "/api/dodocards")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._maybe_log(request, response)
        except Exception:
            pass  # l'audit ne doit jamais casser la requête
        return response

    def _maybe_log(self, request, response):
        if request.method not in WRITE_METHODS:
            return
        if not request.path.startswith(TRACKED_PREFIXES):
            return
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return
        if response.status_code >= 400:
            return
        from .models import AuditLog

        AuditLog.objects.create(
            user=user,
            username=user.username,
            action=f"{request.method} {request.path}",
            ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
            method=request.method,
            path=request.path[:300],
        )
