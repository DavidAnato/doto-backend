"""Logique consentement accès dossier + notifications SSE/push."""
from __future__ import annotations

from django.utils import timezone

from audit.models import AuditLog
from cards.pubsub import hub_bus
from core.permissions import Roles, role_can
from notifications.models import Notification
from notifications.services import notify_user

from .models import AccessBlock, AccessRequest, default_grant_expiry, default_request_expiry


def is_access_blocked(requester, patient) -> AccessBlock | None:
    """Retourne le blocage actif si le pro ou sa structure est blacklisté."""
    qs = AccessBlock.objects.filter(patient=patient, active=True)
    structure = getattr(requester, "structure_principale", None)
    block = qs.filter(blocked_user=requester).first()
    if block:
        return block
    if structure:
        return qs.filter(blocked_structure=structure).first()
    return None


def _role_label(user) -> str:
    try:
        return user.get_role_display()
    except Exception:
        return getattr(user, "role", "")


def _requester_name(user) -> str:
    return (user.get_full_name() or user.username or "").strip()


def _structure_name(user) -> str:
    sp = getattr(user, "structure_principale", None)
    return sp.nom if sp else ""


def _consent_intent_for_role(role: str) -> str:
    """Phrase d'intention contextualisée selon le rôle du demandeur."""
    return {
        Roles.PHARMACIEN: "souhaite consulter vos ordonnances",
        Roles.LABORANTIN: "souhaite consulter vos examens",
        Roles.INFIRMIER: "souhaite consulter vos constantes et notes de soins",
        Roles.MEDECIN: "souhaite consulter votre dossier médical",
        Roles.RECEPTIONNISTE: "souhaite vérifier votre identité et votre assurance",
        Roles.AMBULANCIER: "souhaite consulter vos informations d'urgence",
        Roles.ADMIN: "souhaite consulter votre dossier médical",
    }.get(role, "souhaite consulter votre dossier")


def _default_reason_for_role(role: str) -> str:
    return {
        Roles.PHARMACIEN: "Consultation des ordonnances",
        Roles.LABORANTIN: "Consultation des examens",
        Roles.INFIRMIER: "Consultation des constantes / soins",
        Roles.MEDECIN: "Consultation du dossier médical",
        Roles.RECEPTIONNISTE: "Vérification identité / assurance",
        Roles.AMBULANCIER: "Consultation urgence",
        Roles.ADMIN: "Consultation du dossier médical",
    }.get(role, "Consultation du dossier médical")


def expire_stale_requests(patient=None, requester=None) -> int:
    now = timezone.now()
    qs = AccessRequest.objects.filter(status=AccessRequest.Status.PENDING, expires_at__lt=now)
    if patient is not None:
        qs = qs.filter(patient=patient)
    if requester is not None:
        qs = qs.filter(requester=requester)
    count = 0
    for req in qs:
        req.status = AccessRequest.Status.EXPIRED
        req.responded_at = now
        req.save(update_fields=["status", "responded_at"])
        count += 1
        AuditLog.objects.create(
            user=req.requester,
            username=req.requester.username,
            action="access_request_expired",
            target=f"access:{req.id}",
            patient_npi=req.patient.npi,
        )
        notify_user(
            req.requester,
            title="Demande d'accès expirée",
            body=f"Le patient {req.patient.full_name} n'a pas répondu à temps.",
            type=Notification.Type.ACCESS_EXPIRED,
            payload={"access_request_id": req.id, "patient_id": req.patient_id},
        )
        hub_bus.publish(
            req.requester_id,
            {
                "type": "access_expired",
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "ts": now.isoformat(),
            },
        )
    return count


def has_active_grant(requester, patient) -> AccessRequest | None:
    expire_stale_requests(patient=patient, requester=requester)
    if is_access_blocked(requester, patient):
        return None
    qs = AccessRequest.objects.filter(
        requester=requester,
        patient=patient,
        status__in=(
            AccessRequest.Status.APPROVED,
            AccessRequest.Status.EMERGENCY_BYPASS,
        ),
    ).order_by("-created_at")
    for req in qs:
        if req.has_active_grant:
            return req
    return None


def assert_patient_access(user, patient, *, allow_admin: bool = True) -> AccessRequest | None:
    """Lève PermissionDenied si le pro n'a pas de grant actif (admin exempt)."""
    from rest_framework.exceptions import PermissionDenied

    from core.permissions import Roles

    if allow_admin and getattr(user, "role", None) == Roles.ADMIN:
        return None
    if getattr(user, "role", None) == Roles.PATIENT:
        own = getattr(user, "patient", None)
        if own and own.pk == patient.pk:
            return None
        raise PermissionDenied("Accès réservé à votre propre dossier.")
    grant = has_active_grant(user, patient)
    if grant_allows_full(grant):
        return grant
    raise PermissionDenied(
        {
            "detail": "Consentement patient requis.",
            "code": "consent_required",
            "consent_required": True,
            "patient_id": patient.pk,
        }
    )


def grant_allows_full(req: AccessRequest | None) -> bool:
    if req is None:
        return False
    return req.has_active_grant


def is_emergency_requester(user, emergency_flag: bool = False) -> bool:
    """Urgence : flag explicite OU rôle ambulancier (accès vital immédiat)."""
    if emergency_flag:
        return True
    return getattr(user, "role", None) == Roles.AMBULANCIER and role_can(
        user.role, "emergency_open"
    )


def create_access_request(
    *,
    requester,
    patient,
    mode: str = AccessRequest.Mode.SCAN,
    reason: str = "",
    dodocard=None,
    emergency: bool = False,
    request=None,
) -> AccessRequest:
    """Crée une demande (ou bypass urgence). Notifie patient / pro."""
    expire_stale_requests(patient=patient)

    # Blacklist : blocage permanent patient → pro / structure
    block = is_access_blocked(requester, patient)
    if block:
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied(
            {
                "detail": "Accès bloqué définitivement par le patient.",
                "code": "access_blocked",
                "access_blocked": True,
                "block_id": block.id,
                "patient_id": patient.pk,
            }
        )

    # Réutiliser un grant encore valide
    existing = has_active_grant(requester, patient)
    if existing and existing.has_active_grant and not emergency:
        existing._reused = True  # type: ignore[attr-defined]
        return existing

    # Réutiliser une demande pending encore valide (évite d'invalider
    # la ConsentCard pendant que le patient répond / double requestAccess).
    if not emergency:
        pending = (
            AccessRequest.objects.filter(
                requester=requester,
                patient=patient,
                status=AccessRequest.Status.PENDING,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if pending:
            pending._reused = True  # type: ignore[attr-defined]
            return pending

    # Annuler les pending du même pro→patient
    AccessRequest.objects.filter(
        requester=requester,
        patient=patient,
        status=AccessRequest.Status.PENDING,
    ).update(status=AccessRequest.Status.EXPIRED, responded_at=timezone.now())

    structure = getattr(requester, "structure_principale", None)
    now = timezone.now()

    if is_emergency_requester(requester, emergency):
        req = AccessRequest.objects.create(
            patient=patient,
            requester=requester,
            structure=structure,
            dodocard=dodocard,
            status=AccessRequest.Status.EMERGENCY_BYPASS,
            mode=AccessRequest.Mode.EMERGENCY,
            reason=reason or "Accès urgence - consentement non requis",
            scope="urgence",
            expires_at=now,
            responded_at=now,
            grant_expires_at=default_grant_expiry(emergency=True),
        )
        AuditLog.objects.create(
            user=requester,
            username=requester.username,
            action="access_emergency_bypass",
            target=f"access:{req.id}",
            patient_npi=patient.npi,
            method=getattr(request, "method", "") if request else "",
            path=getattr(request, "path", "")[:300] if request else "",
        )
        # Notifier patient (info, pas de consentement)
        patient_user = getattr(patient, "user", None)
        if patient_user:
            notify_user(
                patient_user,
                title="Accès urgence à votre dossier",
                body=(
                    f"{_requester_name(requester)} ({_role_label(requester)}) "
                    f"a ouvert votre dossier en mode urgence"
                    + (f" - {_structure_name(requester)}" if _structure_name(requester) else "")
                    + ". Le consentement n'est pas requis en situation vitale."
                ),
                type=Notification.Type.EMERGENCY,
                payload={
                    "access_request_id": req.id,
                    "requester_id": requester.id,
                    "emergency": True,
                },
            )
        # Pro : accès immédiat
        hub_bus.publish(
            requester.id,
            {
                "type": "access_granted",
                "access_request_id": req.id,
                "patient_id": patient.id,
                "npi": patient.npi,
                "full_name": patient.full_name,
                "emergency": True,
                "ts": now.isoformat(),
            },
        )
        notify_user(
            requester,
            title="Accès urgence accordé",
            body=f"Dossier {patient.full_name} - bypass consentement (urgence).",
            type=Notification.Type.EMERGENCY,
            payload={"access_request_id": req.id, "patient_id": patient.id, "emergency": True},
            push=False,
        )
        return req

    req = AccessRequest.objects.create(
        patient=patient,
        requester=requester,
        structure=structure,
        dodocard=dodocard,
        status=AccessRequest.Status.PENDING,
        mode=mode,
        reason=reason or _default_reason_for_role(getattr(requester, "role", "")),
        scope="full",
        expires_at=default_request_expiry(),
    )
    AuditLog.objects.create(
        user=requester,
        username=requester.username,
        action="access_request_created",
        target=f"access:{req.id}",
        patient_npi=patient.npi,
        method=getattr(request, "method", "") if request else "",
        path=getattr(request, "path", "")[:300] if request else "",
    )

    payload = {
        "access_request_id": req.id,
        "requester_id": requester.id,
        "requester_name": _requester_name(requester),
        "requester_role": requester.role,
        "requester_role_label": _role_label(requester),
        "requester_photo_url": None,
        "structure": _structure_name(requester),
        "reason": req.reason,
        "mode": req.mode,
        "patient_id": patient.id,
        "expires_at": req.expires_at.isoformat(),
    }
    try:
        from accounts.photo_utils import user_photo_url

        payload["requester_photo_url"] = user_photo_url(requester)
    except Exception:
        pass

    patient_user = getattr(patient, "user", None)
    if patient_user:
        # Obligatoire AVANT tout grant : notif in-app + SSE (ConsentCard / Alertes).
        intent = _consent_intent_for_role(getattr(requester, "role", ""))
        notify_user(
            patient_user,
            title="Demande d'accès à votre dossier",
            body=(
                f"{payload['requester_name']} ({payload['requester_role_label']})"
                + (f" - {payload['structure']}" if payload["structure"] else "")
                + f" {intent}. Confirmez ou refusez."
            ),
            type=Notification.Type.ACCESS_REQUEST,
            payload=payload,
        )
        hub_bus.publish(
            patient_user.id,
            {
                "type": "access_request",
                **payload,
                "ts": now.isoformat(),
            },
        )
    else:
        # Pas de compte patient lié → impossible de consentir ; le pro reste bloqué.
        AuditLog.objects.create(
            user=requester,
            username=requester.username,
            action="access_request_no_patient_user",
            target=f"access:{req.id}",
            patient_npi=patient.npi,
        )

    # Pro : état pending. Mode SCAN : ScanView publie déjà `dodocard_scan`
    # (évite double toast / nav sur le Hub).
    if mode != AccessRequest.Mode.SCAN:
        hub_bus.publish(
            requester.id,
            {
                "type": "access_pending",
                "access_request_id": req.id,
                "patient_id": patient.id,
                "npi": patient.npi,
                "full_name": patient.full_name,
                "expires_at": req.expires_at.isoformat(),
                "ts": now.isoformat(),
            },
        )
    return req


def respond_access_request(req: AccessRequest, *, approve: bool, patient_user) -> AccessRequest:
    expire_stale_requests(patient=req.patient)
    req.refresh_from_db()
    if req.status != AccessRequest.Status.PENDING:
        return req
    if timezone.now() >= req.expires_at:
        req.status = AccessRequest.Status.EXPIRED
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "responded_at"])
        return req

    now = timezone.now()
    req.responded_at = now
    if approve:
        req.status = AccessRequest.Status.APPROVED
        req.grant_expires_at = default_grant_expiry()
        action = "access_request_approved"
        notif_type = Notification.Type.ACCESS_GRANTED
        title = "Accès autorisé par le patient"
        body = f"{req.patient.full_name} a confirmé l'accès à son dossier."
        event_type = "access_granted"
    else:
        req.status = AccessRequest.Status.DENIED
        action = "access_request_denied"
        notif_type = Notification.Type.ACCESS_DENIED
        title = "Accès refusé par le patient"
        body = f"{req.patient.full_name} a refusé l'accès à son dossier."
        event_type = "access_denied"

    req.save(update_fields=["status", "responded_at", "grant_expires_at"])
    AuditLog.objects.create(
        user=patient_user,
        username=getattr(patient_user, "username", ""),
        action=action,
        target=f"access:{req.id}",
        patient_npi=req.patient.npi,
    )

    hub_bus.publish(
        req.requester_id,
        {
            "type": event_type,
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "npi": req.patient.npi,
            "full_name": req.patient.full_name,
            "emergency": False,
            "ts": now.isoformat(),
        },
    )
    notify_user(
        req.requester,
        title=title,
        body=body,
        type=notif_type,
        payload={
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "approved": approve,
        },
    )
    # Confirmation patient UNIQUEMENT après son action explicite (pas avant le grant).
    if approve:
        notify_user(
            patient_user,
            title="Vous avez autorisé l'accès",
            body=(
                f"Accès accordé à {_requester_name(req.requester)} "
                f"({_role_label(req.requester)}) jusqu'à "
                f"{req.grant_expires_at.strftime('%H:%M') if req.grant_expires_at else 'expiration'}."
            ),
            type=Notification.Type.ACCESS_GRANTED,
            payload={
                "access_request_id": req.id,
                "requester_id": req.requester_id,
                "approved": True,
                "confirmation": True,
            },
            push=False,
        )
    else:
        notify_user(
            patient_user,
            title="Vous avez refusé l'accès",
            body=(
                f"Demande de {_requester_name(req.requester)} "
                f"({_role_label(req.requester)}) refusée."
            ),
            type=Notification.Type.ACCESS_DENIED,
            payload={
                "access_request_id": req.id,
                "requester_id": req.requester_id,
                "approved": False,
                "confirmation": True,
            },
            push=False,
        )
    return req


def cancel_access_request(req: AccessRequest, *, requester) -> AccessRequest:
    """Pro : annule sa propre demande encore en attente (retire la notif patient)."""
    expire_stale_requests(patient=req.patient, requester=requester)
    req.refresh_from_db()
    if req.requester_id != requester.id and getattr(requester, "role", None) != Roles.ADMIN:
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("Vous ne pouvez annuler que vos propres demandes.")
    if req.status != AccessRequest.Status.PENDING:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"detail": "Seule une demande en attente peut être annulée."})

    now = timezone.now()
    req.status = AccessRequest.Status.CANCELLED
    req.responded_at = now
    req.save(update_fields=["status", "responded_at"])
    AuditLog.objects.create(
        user=requester,
        username=getattr(requester, "username", ""),
        action="access_request_cancelled",
        target=f"access:{req.id}",
        patient_npi=req.patient.npi,
    )
    patient_user = getattr(req.patient, "user", None)
    if patient_user:
        notify_user(
            patient_user,
            title="Demande d'accès annulée",
            body=(
                f"{_requester_name(requester)} ({_role_label(requester)}) "
                f"a annulé sa demande d'accès."
            ),
            type=Notification.Type.SYSTEM,
            payload={"access_request_id": req.id, "cancelled": True},
            push=True,
        )
        hub_bus.publish(
            patient_user.id,
            {
                "type": "access_cancelled",
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "ts": now.isoformat(),
            },
        )
    hub_bus.publish(
        requester.id,
        {
            "type": "access_cancelled",
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "ts": now.isoformat(),
        },
    )
    return req


def revoke_access_grant(req: AccessRequest, *, patient_user) -> AccessRequest:
    """Patient : révoque un grant actif (approuvé ou urgence)."""
    if not req.has_active_grant:
        return req
    now = timezone.now()
    req.status = AccessRequest.Status.REVOKED
    req.grant_expires_at = now
    req.responded_at = now
    req.save(update_fields=["status", "grant_expires_at", "responded_at"])
    AuditLog.objects.create(
        user=patient_user,
        username=getattr(patient_user, "username", ""),
        action="access_request_revoked",
        target=f"access:{req.id}",
        patient_npi=req.patient.npi,
    )
    msg = (
        f"{req.patient.full_name} a retiré l'accès à son dossier. "
        "Le dossier se ferme."
    )
    # Event typé prioritaire (fermeture dossier temps réel côté Hub web + mobile)
    hub_bus.publish(
        req.requester_id,
        {
            "type": "access_revoked",
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "npi": req.patient.npi,
            "full_name": req.patient.full_name,
            "message": msg,
            "close_dossier": True,
            "ts": now.isoformat(),
        },
    )
    notify_user(
        req.requester,
        title="Accès révoqué par le patient",
        body=msg,
        type=Notification.Type.ACCESS_DENIED,
        payload={
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "revoked": True,
            "close_dossier": True,
            "message": msg,
        },
    )
    notify_user(
        patient_user,
        title="Accès révoqué",
        body=(
            f"Vous avez retiré l'accès de {_requester_name(req.requester)} "
            f"({_role_label(req.requester)})."
        ),
        type=Notification.Type.SYSTEM,
        payload={"access_request_id": req.id, "revoked": True},
        push=False,
    )
    return req


def serialize_access_request(req: AccessRequest) -> dict:
    from accounts.photo_utils import patient_photo_url, user_photo_url

    return {
        "id": req.id,
        "patient_id": req.patient_id,
        "patient_name": req.patient.full_name,
        "patient_npi": req.patient.npi,
        "patient_photo_url": patient_photo_url(req.patient),
        "requester_id": req.requester_id,
        "requester_name": _requester_name(req.requester),
        "requester_role": req.requester.role,
        "requester_role_label": _role_label(req.requester),
        "requester_photo_url": user_photo_url(req.requester),
        "structure": req.structure.nom if req.structure else _structure_name(req.requester),
        "status": req.status,
        "mode": req.mode,
        "reason": req.reason,
        "scope": req.scope,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "expires_at": req.expires_at.isoformat() if req.expires_at else None,
        "responded_at": req.responded_at.isoformat() if req.responded_at else None,
        "grant_expires_at": req.grant_expires_at.isoformat() if req.grant_expires_at else None,
        "has_active_grant": req.has_active_grant,
    }
