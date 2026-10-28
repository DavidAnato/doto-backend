"""Hôpital mère : accès direct, demandes externes, durée, activation téléphone.

L'hôpital qui crée le dossier est l'hôpital mère. Ses professionnels ouvrent
le dossier tout de suite. Une autre structure ne voit rien tant que l'accueil
(ou le médecin référent) n'a pas accepté, pour la durée demandée.

Si le patient active ensuite l'application, les demandes extérieures lui
reviennent. Le flux « patient qui valide lui-même » reste inchangé pour
les dossiers déjà reliés à un téléphone.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditLog
from cards.pubsub import hub_bus
from core.permissions import Roles
from notifications.models import Notification
from notifications.services import notify_user

from .models import AccessRequest, HopitalMereTransfer, Patient, default_request_expiry

User = get_user_model()

MAX_DURATION_HOURS = 24 * 366
DESK_ROLES = (Roles.RECEPTIONNISTE, Roles.ADMIN)


def duration_label(hours) -> str | None:
    if not hours:
        return None
    hours = int(hours)
    if hours % 24 == 0:
        days = hours // 24
        return "1 jour" if days == 1 else f"{days} jours"
    return "1 heure" if hours == 1 else f"{hours} heures"


def parse_duration_hours(data) -> int | None:
    """Lit duration_hours ou duration_days. None si absent."""
    if data is None:
        return None
    raw = data.get("duration_hours")
    if raw in (None, "") and data.get("duration_days") not in (None, ""):
        try:
            raw = int(data.get("duration_days")) * 24
        except (TypeError, ValueError):
            raise ValidationError({"duration_hours": "Durée invalide."})
    if raw in (None, ""):
        return None
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"duration_hours": "Durée invalide."})
    if hours < 1 or hours > MAX_DURATION_HOURS:
        raise ValidationError(
            {"duration_hours": "La durée doit être comprise entre 1 heure et 366 jours."}
        )
    return hours


def patient_controls_external_requests(patient) -> bool:
    """Vrai dès que le dossier est relié à un compte application."""
    return bool(getattr(patient, "user_id", None))


def user_structure_id(user) -> int | None:
    return getattr(user, "structure_principale_id", None)


def _name(user) -> str:
    if user is None:
        return ""
    return (user.get_full_name() or user.username or "").strip()


def desk_users(structure_id):
    if not structure_id:
        return User.objects.none()
    return User.objects.filter(
        actif=True,
        structure_principale_id=structure_id,
        role__in=DESK_ROLES,
    )


def has_structure_direct_access(user, patient) -> bool:
    """Professionnel de l'hôpital mère, transfert en cours, ou accès déjà ouvert conservé."""
    sid = user_structure_id(user)
    if not sid or patient is None:
        return False
    if patient.hopital_mere_id and sid == patient.hopital_mere_id:
        return True
    if HopitalMereTransfer.objects.filter(
        patient=patient,
        status__in=(
            HopitalMereTransfer.Status.PENDING,
            HopitalMereTransfer.Status.EN_COURS,
        ),
    ).filter(Q(from_structure_id=sid) | Q(to_structure_id=sid)).exists():
        return True
    legacy = (
        HopitalMereTransfer.objects.filter(
            patient=patient,
            status=HopitalMereTransfer.Status.CONFIRME,
            from_structure_id=sid,
            legacy_access_until__gt=timezone.now(),
        )
        .order_by("-confirmed_at", "-id")
        .first()
    )
    if legacy is None:
        return False
    opened_before = legacy.confirmed_at or legacy.created_at
    opened = AccessRequest.objects.filter(
        patient=patient,
        requester=user,
        status=AccessRequest.Status.APPROVED,
        created_at__lte=opened_before,
    ).order_by("-id")
    from patients.access import grant_deadline_passed

    for req in opened[:20]:
        if grant_deadline_passed(req):
            continue
        return True
    return False


def bypasses_consent(user, patient) -> bool:
    """Superuser, ou admin sur un dossier sans hôpital mère (comptes déjà en place)."""
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "role", None) != Roles.ADMIN:
        return False
    if not getattr(patient, "hopital_mere_id", None):
        return True
    return has_structure_direct_access(user, patient)


def audit_dossier_open(user, patient) -> None:
    sid = user_structure_id(user) or ""
    AuditLog.objects.create(
        user=user,
        username=getattr(user, "username", ""),
        action="ouverture_dossier",
        target=f"structure:{sid}"[:255],
        patient_npi=patient.npi,
    )


def ensure_direct_grant(requester, patient, *, mode=AccessRequest.Mode.SCAN, dodocard=None):
    """Grant immédiat, réutilisé tant que la structure a encore l'accès direct."""
    existing = (
        AccessRequest.objects.filter(
            requester=requester,
            patient=patient,
            direct_structure=True,
            status=AccessRequest.Status.APPROVED,
        )
        .order_by("-id")
        .first()
    )
    now = timezone.now()
    if existing:
        existing.last_accessed_at = now
        existing.save(update_fields=["last_accessed_at"])
        existing._reused = True  # type: ignore[attr-defined]
        return existing

    AccessRequest.objects.filter(
        requester=requester,
        patient=patient,
        status__in=(AccessRequest.Status.PENDING, AccessRequest.Status.PENDING_PRIOR),
    ).update(status=AccessRequest.Status.EXPIRED, responded_at=now)

    structure_id = user_structure_id(requester)
    req = AccessRequest.objects.create(
        patient=patient,
        requester=requester,
        structure_id=structure_id,
        dodocard=dodocard,
        status=AccessRequest.Status.APPROVED,
        mode=mode,
        route=AccessRequest.Route.HOPITAL_MERE,
        reason="Accès immédiat — hôpital mère",
        scope="full",
        direct_structure=True,
        expires_at=now,
        responded_at=now,
        grant_expires_at=None,
        last_accessed_at=now,
        decision_structure_id=patient.hopital_mere_id,
    )
    AuditLog.objects.create(
        user=requester,
        username=getattr(requester, "username", ""),
        action="access_direct_hopital_mere",
        target=f"structure:{structure_id or ''}|access:{req.id}"[:255],
        patient_npi=patient.npi,
    )
    return req


def needs_duration_gate(user, patient, *, emergency: bool) -> bool:
    """Vrai si le scan doit demander une durée avant d'envoyer la demande."""
    from patients.access import is_emergency_requester

    if emergency or is_emergency_requester(user, emergency):
        return False
    if has_structure_direct_access(user, patient):
        return False
    if patient_controls_external_requests(patient):
        return False
    return bool(patient.hopital_mere_id)


def _notify_approvers(req: AccessRequest) -> None:
    patient = req.patient
    structure_id = req.decision_structure_id or patient.hopital_mere_id
    label = duration_label(req.requested_duration_hours) or "la durée indiquée"
    body = (
        f"{_name(req.requester)} ({getattr(req.requester, 'role', '')}) "
        f"demande l'accès au dossier de {patient.full_name} pour {label}. "
        "Acceptez ou refusez."
    )
    payload = {
        "access_request_id": req.id,
        "patient_id": patient.id,
        "route": AccessRequest.Route.HOPITAL_MERE,
        "requested_duration_hours": req.requested_duration_hours,
        "inbox": True,
    }
    recipients = list(desk_users(structure_id))
    referent = getattr(patient, "medecin_traitant", None)
    if referent is not None and referent.actif:
        recipients.append(referent)
    seen = set()
    for user in recipients:
        if user.id in seen or user.id == req.requester_id:
            continue
        seen.add(user.id)
        notify_user(
            user,
            title="Demande d'accès à valider",
            body=body,
            type=Notification.Type.ACCESS_REQUEST,
            payload=payload,
        )
        hub_bus.publish(
            user.id,
            {
                "type": "access_hopital_mere",
                "access_request_id": req.id,
                "patient_id": patient.id,
                "patient_name": patient.full_name,
                "route": AccessRequest.Route.HOPITAL_MERE,
                "ts": timezone.now().isoformat(),
            },
        )


def create_hopital_mere_request(
    *,
    requester,
    patient,
    mode: str,
    reason: str,
    dodocard=None,
    duration_hours: int | None,
    request=None,
) -> AccessRequest:
    if patient_controls_external_requests(patient):
        raise ValidationError({"detail": "Le patient valide lui-même cette demande."})
    if not patient.hopital_mere_id:
        raise ValidationError(
            {
                "detail": (
                    "Aucun hôpital mère : l'accueil doit rattacher le dossier "
                    "avant qu'une autre structure y accède."
                ),
                "code": "no_hopital_mere",
            }
        )
    if not duration_hours:
        raise ValidationError(
            {
                "detail": "Indiquez la durée d'accès. Le dossier reste fermé.",
                "code": "duration_required",
                "duration_required": True,
                "patient_id": patient.pk,
            }
        )

    now = timezone.now()
    pending = (
        AccessRequest.objects.filter(
            requester=requester,
            patient=patient,
            route=AccessRequest.Route.HOPITAL_MERE,
            status=AccessRequest.Status.PENDING,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )
    if pending:
        pending._reused = True  # type: ignore[attr-defined]
        return pending

    AccessRequest.objects.filter(
        requester=requester,
        patient=patient,
        status__in=(AccessRequest.Status.PENDING, AccessRequest.Status.PENDING_PRIOR),
    ).update(status=AccessRequest.Status.EXPIRED, responded_at=now)

    req = AccessRequest.objects.create(
        patient=patient,
        requester=requester,
        structure_id=user_structure_id(requester),
        dodocard=dodocard,
        decision_structure_id=patient.hopital_mere_id,
        status=AccessRequest.Status.PENDING,
        mode=mode,
        route=AccessRequest.Route.HOPITAL_MERE,
        reason=reason or "Demande d'accès hors hôpital mère",
        scope="full",
        requested_duration_hours=duration_hours,
        expires_at=default_request_expiry(),
    )
    AuditLog.objects.create(
        user=requester,
        username=getattr(requester, "username", ""),
        action="access_request_hopital_mere",
        target=f"access:{req.id}|structure:{patient.hopital_mere_id}",
        patient_npi=patient.npi,
        method=getattr(request, "method", "") if request else "",
        path=getattr(request, "path", "")[:300] if request else "",
    )
    _notify_approvers(req)
    if mode != AccessRequest.Mode.SCAN:
        hub_bus.publish(
            requester.id,
            {
                "type": "access_pending",
                "access_request_id": req.id,
                "patient_id": patient.id,
                "npi": patient.npi,
                "full_name": patient.full_name,
                "route": AccessRequest.Route.HOPITAL_MERE,
                "dossier_visible": False,
                "expires_at": req.expires_at.isoformat(),
                "ts": now.isoformat(),
            },
        )
    return req


def can_decide_hopital_mere(user, patient, req: AccessRequest | None = None) -> bool:
    if patient_controls_external_requests(patient):
        return False
    if req is not None and req.requester_id == getattr(user, "id", None):
        return False
    role = getattr(user, "role", None)
    sid = user_structure_id(user)
    target = None
    if req is not None and req.decision_structure_id:
        target = req.decision_structure_id
    else:
        target = patient.hopital_mere_id
    if role in DESK_ROLES and sid and target and sid == target:
        return True
    if (
        role == Roles.MEDECIN
        and patient.medecin_traitant_id
        and patient.medecin_traitant_id == getattr(user, "id", None)
    ):
        return True
    return False


def access_inbox_qs(user):
    base = AccessRequest.objects.filter(
        status=AccessRequest.Status.PENDING,
        route=AccessRequest.Route.HOPITAL_MERE,
        patient__user__isnull=True,
    ).select_related(
        "requester", "structure", "patient", "patient__hopital_mere",
        "prior_medecin", "decision_structure", "decided_by",
    )
    role = getattr(user, "role", None)
    sid = user_structure_id(user)
    parts = Q()
    matched = False
    if role in DESK_ROLES and sid:
        parts |= Q(decision_structure_id=sid) | Q(
            decision_structure__isnull=True, patient__hopital_mere_id=sid
        )
        matched = True
    if role == Roles.MEDECIN:
        parts |= Q(patient__medecin_traitant_id=user.id)
        matched = True
    if not matched:
        return base.none()
    return base.filter(parts)


def hopital_mere_respond(req: AccessRequest, *, approve: bool, actor) -> AccessRequest:
    from patients.access import expire_stale_requests

    expire_stale_requests(patient=req.patient)
    req.refresh_from_db()
    if patient_controls_external_requests(req.patient) or req.route != AccessRequest.Route.HOPITAL_MERE:
        raise ValidationError(
            {"detail": "Cette demande est adressée au patient, plus à l'hôpital mère."}
        )
    if req.status != AccessRequest.Status.PENDING:
        return req
    now = timezone.now()
    if req.expires_at and now >= req.expires_at:
        req.status = AccessRequest.Status.EXPIRED
        req.responded_at = now
        req.save(update_fields=["status", "responded_at"])
        return req
    if not can_decide_hopital_mere(actor, req.patient, req):
        raise PermissionDenied(
            "Seul l'accueil de l'hôpital mère, ou le médecin référent, peut répondre."
        )

    if not approve:
        req.status = AccessRequest.Status.DENIED
        req.responded_at = now
        req.decided_by = actor
        req.save(update_fields=["status", "responded_at", "decided_by"])
        AuditLog.objects.create(
            user=actor,
            username=getattr(actor, "username", ""),
            action="access_request_denied_hopital_mere",
            target=f"access:{req.id}|structure:{user_structure_id(actor) or ''}",
            patient_npi=req.patient.npi,
        )
        hub_bus.publish(
            req.requester_id,
            {
                "type": "access_denied",
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "npi": req.patient.npi,
                "full_name": req.patient.full_name,
                "hopital_denied": True,
                "ts": now.isoformat(),
            },
        )
        notify_user(
            req.requester,
            title="Accès refusé par l'hôpital mère",
            body=f"L'hôpital mère a refusé l'accès au dossier de {req.patient.full_name}.",
            type=Notification.Type.ACCESS_DENIED,
            payload={"access_request_id": req.id, "patient_id": req.patient_id, "hopital_denied": True},
        )
        return req

    hours = int(req.requested_duration_hours or 0)
    if hours < 1:
        raise ValidationError({"detail": "Cette demande n'a pas de durée."})
    req.status = AccessRequest.Status.APPROVED
    req.responded_at = now
    req.decided_by = actor
    req.grant_expires_at = now + timezone.timedelta(hours=hours)
    req.last_accessed_at = now
    req.save(
        update_fields=[
            "status",
            "responded_at",
            "decided_by",
            "grant_expires_at",
            "last_accessed_at",
        ]
    )
    AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", ""),
        action="access_request_approved_hopital_mere",
        target=f"access:{req.id}|structure:{user_structure_id(actor) or ''}|{hours}h",
        patient_npi=req.patient.npi,
    )
    label = duration_label(hours)
    hub_bus.publish(
        req.requester_id,
        {
            "type": "access_granted",
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "npi": req.patient.npi,
            "full_name": req.patient.full_name,
            "emergency": False,
            "permanent": False,
            "grant_expires_at": req.grant_expires_at.isoformat(),
            "ts": now.isoformat(),
        },
    )
    notify_user(
        req.requester,
        title="Accès autorisé",
        body=(
            f"Accès au dossier de {req.patient.full_name} accordé pour {label}. "
            "Il se fermera automatiquement à l'échéance."
        ),
        type=Notification.Type.ACCESS_GRANTED,
        payload={
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "approved": True,
            "permanent": False,
            "grant_expires_at": req.grant_expires_at.isoformat(),
        },
    )
    return req


def notify_mere_of_emergency(patient, requester, req: AccessRequest) -> None:
    """Urgence vitale : l'hôpital mère est informé après l'ouverture, sans la bloquer."""
    mere_id = getattr(patient, "hopital_mere_id", None)
    if not mere_id:
        return
    if user_structure_id(requester) == mere_id:
        return
    body = (
        f"{_name(requester)} a ouvert les données critiques de {patient.full_name} "
        "en urgence vitale. L'accès immédiat est journalisé."
    )
    payload = {
        "access_request_id": req.id,
        "patient_id": patient.id,
        "emergency": True,
        "hopital_mere_informe": True,
    }
    recipients = list(desk_users(mere_id))
    referent = getattr(patient, "medecin_traitant", None)
    if referent is not None and getattr(referent, "actif", True):
        recipients.append(referent)
    seen = set()
    for user in recipients:
        if not user or user.id in seen or user.id == getattr(requester, "id", None):
            continue
        seen.add(user.id)
        notify_user(
            user,
            title="Urgence vitale sur un dossier",
            body=body,
            type=Notification.Type.EMERGENCY,
            payload=payload,
        )


def _open_transfer_brief(patient) -> dict | None:
    active = (
        HopitalMereTransfer.objects.filter(
            patient=patient,
            status__in=(
                HopitalMereTransfer.Status.PENDING,
                HopitalMereTransfer.Status.EN_COURS,
            ),
        )
        .select_related("from_structure", "to_structure")
        .order_by("-id")
        .first()
    )
    if active is None:
        return None
    return {
        "id": active.id,
        "status": active.status,
        "status_label": active.get_status_display(),
        "from_structure_id": active.from_structure_id,
        "from_structure_nom": active.from_structure.nom,
        "to_structure_id": active.to_structure_id,
        "to_structure_nom": active.to_structure.nom,
        "from_approved": bool(active.from_approved_at),
        "to_approved": bool(active.to_approved_at),
        "duration_hours": active.duration_hours,
    }


def etablissement_payload(user, patient) -> dict:
    mere = getattr(patient, "hopital_mere", None)
    sid = user_structure_id(user)
    role = getattr(user, "role", None)
    is_desk = role in DESK_ROLES and sid and mere and sid == mere.id
    is_other_desk = role in DESK_ROLES and sid and mere and sid != mere.id
    referent = getattr(patient, "medecin_traitant", None)
    return {
        "hopital_mere_id": mere.id if mere else None,
        "hopital_mere_nom": mere.nom if mere else None,
        "app_activee": patient_controls_external_requests(patient),
        "medecin_referent_id": patient.medecin_traitant_id,
        "medecin_referent_nom": _name(referent) or None,
        "peut_emettre_carte": bool(is_desk),
        "peut_designer_referent": bool(is_desk),
        "peut_lancer_transfert": False,
        "peut_demander_transfert": bool(is_other_desk),
        "transfert": _open_transfer_brief(patient),
    }


def masked_dossier_payload(patient, pending, *, user) -> dict:
    """Identité seule : aucune donnée clinique tant que l'hôpital mère n'a pas accepté."""
    from accounts.photo_utils import patient_photo_url

    consent = {
        "required": True,
        "granted": False,
        "pending": bool(pending),
        "dossier_masque": True,
        "route": AccessRequest.Route.HOPITAL_MERE,
        "needs_duration": pending is None,
        "can_request": True,
        "awaiting_hopital_mere": True,
        "access_request_id": pending.id if pending else None,
        "message": (
            "Demande envoyée à l'hôpital mère. Le dossier reste fermé tant qu'elle n'a pas accepté."
            if pending
            else "Dossier fermé. Indiquez la durée demandée : la demande part à l'hôpital mère."
        ),
    }
    if pending and pending.requested_duration_hours:
        consent["requested_duration_hours"] = pending.requested_duration_hours
        consent["duration_label"] = duration_label(pending.requested_duration_hours)
    return {
        "id": patient.id,
        "npi": patient.npi,
        "nom": patient.nom,
        "prenom": patient.prenom,
        "full_name": patient.full_name,
        "photo_url": patient_photo_url(patient),
        "date_naissance": patient.date_naissance.isoformat() if patient.date_naissance else None,
        "consent": consent,
        "etablissement": etablissement_payload(user, patient),
    }


def grant_is_live(req: AccessRequest) -> bool:
    from patients.access import grant_deadline_passed

    if grant_deadline_passed(req) or not req.has_active_grant:
        return False
    if req.direct_structure:
        return has_structure_direct_access(req.requester, req.patient)
    return True
