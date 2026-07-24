from .models import AuditLog


def client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(request, action, target="", patient_npi=""):
    """Enregistre une action métier dans le journal d'audit."""
    user = getattr(request, "user", None)
    AuditLog.objects.create(
        user=user if (user and user.is_authenticated) else None,
        username=getattr(user, "username", "") if user and user.is_authenticated else "anonyme",
        action=action,
        target=str(target)[:255],
        patient_npi=patient_npi,
        ip=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
        method=request.method,
        path=request.path[:300],
    )
