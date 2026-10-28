"""Logique consentement accès dossier + notifications SSE/push."""
from __future__ import annotations

from datetime import timezone as dt_timezone

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from cards.pubsub import hub_bus
from core.permissions import Roles, role_can
from notifications.models import Notification
from notifications.services import notify_user

from .models import AccessBlock, AccessRequest, default_grant_expiry, default_prior_expiry, default_request_expiry


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


def _as_utc(value):
    """Compare toujours en UTC. Une valeur naïve (SQLite) est lue dans le fuseau du projet."""
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value.astimezone(dt_timezone.utc)


def grant_deadline_passed(req, now=None) -> bool:
    """Vrai dès l'instant de fin, y compris à l'égalité. None = durable, sauf urgence ou durée demandée."""
    now_utc = _as_utc(now or timezone.now())
    status = getattr(req, "status", None)
    end = getattr(req, "grant_expires_at", None)
    if status == AccessRequest.Status.EMERGENCY_BYPASS:
        if end is None:
            anchor = getattr(req, "responded_at", None) or getattr(req, "created_at", None)
            if anchor is None:
                return True
            minutes = int(getattr(settings, "ACCESS_EMERGENCY_GRANT_TTL_MINUTES", 30))
            end = anchor + timezone.timedelta(minutes=minutes)
        return now_utc >= _as_utc(end)
    if end is None:
        hours = getattr(req, "requested_duration_hours", None)
        if hours and not getattr(req, "direct_structure", False):
            anchor = getattr(req, "responded_at", None) or getattr(req, "created_at", None)
            if anchor is None:
                return True
            end = anchor + timezone.timedelta(hours=int(hours))
            return now_utc >= _as_utc(end)
        return False
    return now_utc >= _as_utc(end)


def _expire_elapsed_grants(patient=None, requester=None) -> int:
    """Ferme les accès à durée limitée une fois l'échéance passée.

    Le filtre SQL seul rate parfois l'échéance (chaîne SQLite / fuseau).
    La date est donc relue en Python à chaque passage.
    """
    now = timezone.now()
    qs = AccessRequest.objects.filter(
        status__in=(
            AccessRequest.Status.APPROVED,
            AccessRequest.Status.EMERGENCY_BYPASS,
        ),
    ).filter(
        Q(grant_expires_at__isnull=False)
        | Q(status=AccessRequest.Status.EMERGENCY_BYPASS)
        | Q(requested_duration_hours__isnull=False, direct_structure=False)
    )
    if patient is not None:
        qs = qs.filter(patient=patient)
    if requester is not None:
        qs = qs.filter(requester=requester)
    count = 0
    for req in qs.select_related("patient", "requester"):
        if not grant_deadline_passed(req, now):
            continue
        req.status = AccessRequest.Status.EXPIRED
        req.responded_at = now
        req.save(update_fields=["status", "responded_at"])
        count += 1
        AuditLog.objects.create(
            user=req.requester,
            username=req.requester.username,
            action="access_grant_expired",
            target=f"access:{req.id}",
            patient_npi=req.patient.npi,
        )
        notify_user(
            req.requester,
            title="Accès terminé",
            body=f"L'accès au dossier de {req.patient.full_name} est arrivé à échéance.",
            type=Notification.Type.ACCESS_EXPIRED,
            payload={"access_request_id": req.id, "patient_id": req.patient_id},
        )
        hub_bus.publish(
            req.requester_id,
            {
                "type": "access_expired",
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "npi": req.patient.npi,
                "full_name": req.patient.full_name,
                "message": "Cet accès a expiré",
                "ts": now.isoformat(),
            },
        )
    return count


def expire_stale_requests(patient=None, requester=None) -> int:
    """Expire les demandes patient (PENDING) et celles de l'ancien médecin (PENDING_PRIOR)."""
    now = timezone.now()
    count = _expire_elapsed_grants(patient=patient, requester=requester)

    # Le patient a déjà accepté ; l'ancien médecin n'a pas confirmé à temps.
    prior_qs = AccessRequest.objects.filter(
        status=AccessRequest.Status.PENDING_PRIOR,
        expires_at__lt=now,
    )
    if patient is not None:
        prior_qs = prior_qs.filter(patient=patient)
    if requester is not None:
        prior_qs = prior_qs.filter(requester=requester)
    for req in prior_qs.select_related("patient", "requester", "prior_medecin"):
        req.status = AccessRequest.Status.EXPIRED
        req.responded_at = now
        req.save(update_fields=["status", "responded_at"])
        count += 1
        AuditLog.objects.create(
            user=req.requester,
            username=req.requester.username,
            action="access_prior_expired",
            target=f"access:{req.id}",
            patient_npi=req.patient.npi,
        )
        notify_user(
            req.requester,
            title="Transfert expiré",
            body=(
                f"L'ancien médecin n'a pas confirmé à temps pour {req.patient.full_name}. "
                "L'accès n'est pas ouvert."
            ),
            type=Notification.Type.ACCESS_EXPIRED,
            payload={"access_request_id": req.id, "patient_id": req.patient_id},
        )
        patient_user = getattr(req.patient, "user", None)
        if patient_user:
            notify_user(
                patient_user,
                title="Transfert expiré",
                body=(
                    "Vous aviez accepté l'accès, mais l'ancien médecin n'a pas confirmé à temps. "
                    "Le dossier n'a pas été transféré."
                ),
                type=Notification.Type.ACCESS_EXPIRED,
                payload={"access_request_id": req.id, "patient_id": req.patient_id},
                push=False,
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

    qs = AccessRequest.objects.filter(
        status=AccessRequest.Status.PENDING,
        expires_at__lt=now,
    )
    if patient is not None:
        qs = qs.filter(patient=patient)
    if requester is not None:
        qs = qs.filter(requester=requester)
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


def resolve_medecin_traitant(patient, excluding_user=None):
    """Médecin traitant du dossier, sinon dernier médecin avec grant actif."""
    mt = getattr(patient, "medecin_traitant", None)
    if mt is not None and (excluding_user is None or mt.id != excluding_user.id):
        return mt
    prior = find_prior_medecin_grant(patient, excluding_user=excluding_user)
    return prior.requester if prior else None


def find_prior_medecin_grant(patient, excluding_user=None) -> AccessRequest | None:
    """Dernier médecin avec accès durable actif (hors demandeur)."""
    qs = (
        AccessRequest.objects.filter(
            patient=patient,
            status=AccessRequest.Status.APPROVED,
            requester__role=Roles.MEDECIN,
        )
        .select_related("requester")
        .order_by("-responded_at", "-created_at")
    )
    if excluding_user is not None:
        qs = qs.exclude(requester_id=excluding_user.id)
    for req in qs:
        if req.has_active_grant:
            return req
    return None


def _finalize_approved_grant(req: AccessRequest, *, actor) -> AccessRequest:
    """Accès durable, ou limité à la durée demandée. Médecin traitant : seulement si durable."""
    now = timezone.now()
    req.status = AccessRequest.Status.APPROVED
    req.responded_at = now
    hours = req.requested_duration_hours
    if hours:
        req.grant_expires_at = now + timezone.timedelta(hours=int(hours))
    else:
        req.grant_expires_at = None
    permanent = req.grant_expires_at is None
    req.last_accessed_at = now
    req.save(
        update_fields=[
            "status",
            "responded_at",
            "grant_expires_at",
            "last_accessed_at",
            "patient_approved_at",
            "prior_medecin",
            "prior_medecin_approved_at",
        ]
    )
    if (
        permanent
        and req.route == AccessRequest.Route.PATIENT
        and getattr(req.requester, "role", None) == Roles.MEDECIN
    ):
        patient = req.patient
        if patient.medecin_traitant_id != req.requester_id:
            patient.medecin_traitant = req.requester
            patient.save(update_fields=["medecin_traitant", "updated_at"])
    AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", ""),
        action="access_request_approved",
        target=f"access:{req.id}",
        patient_npi=req.patient.npi,
    )
    hub_bus.publish(
        req.requester_id,
        {
            "type": "access_granted",
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "npi": req.patient.npi,
            "full_name": req.patient.full_name,
            "emergency": False,
            "permanent": permanent,
            "grant_expires_at": req.grant_expires_at.isoformat() if req.grant_expires_at else None,
            "ts": now.isoformat(),
        },
    )
    from .hopital import duration_label

    if permanent:
        grant_body = f"Accès durable au dossier de {req.patient.full_name} accordé."
    else:
        grant_body = (
            f"Accès au dossier de {req.patient.full_name} accordé pour "
            f"{duration_label(hours)}. Il se fermera automatiquement à l'échéance."
        )
    notify_user(
        req.requester,
        title="Accès autorisé",
        body=grant_body,
        type=Notification.Type.ACCESS_GRANTED,
        payload={
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "approved": True,
            "permanent": permanent,
            "grant_expires_at": req.grant_expires_at.isoformat() if req.grant_expires_at else None,
        },
    )
    return req


def _consent_payload(req: AccessRequest) -> dict:
    payload = {
        "access_request_id": req.id,
        "requester_id": req.requester_id,
        "requester_name": _requester_name(req.requester),
        "requester_role": req.requester.role,
        "requester_role_label": _role_label(req.requester),
        "requester_photo_url": None,
        "structure": req.structure.nom if req.structure else _structure_name(req.requester),
        "reason": req.reason,
        "mode": req.mode,
        "patient_id": req.patient_id,
        "expires_at": req.expires_at.isoformat() if req.expires_at else None,
        "requested_duration_hours": req.requested_duration_hours,
        "route": req.route,
    }
    try:
        from accounts.photo_utils import user_photo_url

        payload["requester_photo_url"] = user_photo_url(req.requester)
    except Exception:
        pass
    return payload


def _notify_prior_medecin(req: AccessRequest) -> None:
    """Ancien médecin : notifié seulement après la validation du patient."""
    prior = req.prior_medecin
    if prior is None:
        return
    notify_user(
        prior,
        title="Transfert à confirmer",
        body=(
            f"{req.patient.full_name} a accepté que {_requester_name(req.requester)} "
            "accède à son dossier. Confirmez le transfert."
        ),
        type=Notification.Type.ACCESS_REQUEST,
        payload={
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "prior_validation": True,
            "patient_already_approved": True,
            "new_medecin_id": req.requester_id,
            "new_medecin_name": _requester_name(req.requester),
        },
    )
    hub_bus.publish(
        prior.id,
        {
            "type": "access_prior_validation",
            "access_request_id": req.id,
            "patient_id": req.patient_id,
            "patient_name": req.patient.full_name,
            "new_medecin_name": _requester_name(req.requester),
            "patient_already_approved": True,
            "ts": timezone.now().isoformat(),
        },
    )


def _notify_patient_consent(req: AccessRequest) -> None:
    """Notifie le patient qu'il doit valider en premier."""
    patient_user = getattr(req.patient, "user", None)
    if not patient_user:
        return
    payload = _consent_payload(req)
    intent = _consent_intent_for_role(getattr(req.requester, "role", ""))
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
            "ts": timezone.now().isoformat(),
        },
    )


def touch_access_session(req: AccessRequest, *, requester, pin: str | None = None) -> AccessRequest:
    """Réouverture d'un grant déjà accordé.

    Si la dernière ouverture est « vieille » : PIN pro requis + notification patient.
    Sinon : simple touch last_accessed_at.
    """
    from rest_framework.exceptions import PermissionDenied, ValidationError

    if not req.has_active_grant:
        raise PermissionDenied("Aucun accès actif.")
    if req.requester_id != requester.id and getattr(requester, "role", None) != Roles.ADMIN:
        raise PermissionDenied("Accès non autorisé.")

    now = timezone.now()
    if req.direct_structure:
        from .hopital import audit_dossier_open, has_structure_direct_access

        if not has_structure_direct_access(requester, req.patient):
            raise PermissionDenied("Accès direct de la structure terminé.")
        audit_dossier_open(requester, req.patient)
        req.last_accessed_at = now
        req.save(update_fields=["last_accessed_at"])
        return req
    stale = req.is_access_stale()
    if stale:
        if not pin:
            raise ValidationError(
                {
                    "detail": "PIN requis : dernière ouverture trop ancienne.",
                    "code": "pin_required",
                    "pin_required": True,
                    "access_request_id": req.id,
                }
            )
        if not requester.check_pin(pin):
            raise PermissionDenied({"detail": "PIN incorrect.", "code": "pin_invalid"})

        patient_user = getattr(req.patient, "user", None)
        if patient_user:
            notify_user(
                patient_user,
                title="Réouverture de votre dossier",
                body=(
                    f"{_requester_name(requester)} ({_role_label(requester)}) "
                    f"a rouvert votre dossier médical."
                ),
                type=Notification.Type.SYSTEM,
                payload={
                    "access_request_id": req.id,
                    "requester_id": requester.id,
                    "reopen": True,
                    "stale": True,
                },
            )
            hub_bus.publish(
                patient_user.id,
                {
                    "type": "access_reopened",
                    "access_request_id": req.id,
                    "requester_id": requester.id,
                    "requester_name": _requester_name(requester),
                    "ts": now.isoformat(),
                },
            )

    req.last_accessed_at = now
    req.save(update_fields=["last_accessed_at"])
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
    if not approve:
        req.status = AccessRequest.Status.DENIED
        req.responded_at = now
        req.save(update_fields=["status", "responded_at"])
        AuditLog.objects.create(
            user=patient_user,
            username=getattr(patient_user, "username", ""),
            action="access_request_denied",
            target=f"access:{req.id}",
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
                "emergency": False,
                "ts": now.isoformat(),
            },
        )
        notify_user(
            req.requester,
            title="Accès refusé par le patient",
            body=f"{req.patient.full_name} a refusé l'accès à son dossier.",
            type=Notification.Type.ACCESS_DENIED,
            payload={"access_request_id": req.id, "patient_id": req.patient_id, "approved": False},
        )
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

    # Patient d'abord. S'il y a un ancien médecin, on le notifie seulement maintenant.
    req.patient_approved_at = now
    if req.prior_medecin_id and req.prior_medecin_approved_at is None:
        req.status = AccessRequest.Status.PENDING_PRIOR
        req.expires_at = default_prior_expiry()
        req.save(update_fields=["patient_approved_at", "status", "expires_at"])
        AuditLog.objects.create(
            user=patient_user,
            username=getattr(patient_user, "username", ""),
            action="access_patient_approved_awaiting_prior",
            target=f"access:{req.id}",
            patient_npi=req.patient.npi,
        )
        _notify_prior_medecin(req)
        notify_user(
            req.requester,
            title="Patient d'accord — en attente de l'ancien médecin",
            body=(
                f"{req.patient.full_name} a accepté. "
                f"{_requester_name(req.prior_medecin)} doit encore confirmer le transfert."
            ),
            type=Notification.Type.SYSTEM,
            payload={
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "awaiting_prior": True,
            },
        )
        notify_user(
            patient_user,
            title="Vous avez accepté — confirmation de l'ancien médecin",
            body=(
                f"Votre accord pour {_requester_name(req.requester)} est enregistré. "
                "L'ancien médecin doit encore confirmer avant l'ouverture du dossier."
            ),
            type=Notification.Type.SYSTEM,
            payload={
                "access_request_id": req.id,
                "requester_id": req.requester_id,
                "approved": True,
                "awaiting_prior": True,
                "confirmation": True,
            },
            push=False,
        )
        hub_bus.publish(
            req.requester_id,
            {
                "type": "access_awaiting_prior",
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "npi": req.patient.npi,
                "full_name": req.patient.full_name,
                "awaiting_prior": True,
                "ts": now.isoformat(),
            },
        )
        return req

    req.responded_at = now
    req = _finalize_approved_grant(req, actor=patient_user)
    notify_user(
        patient_user,
        title="Vous avez autorisé l'accès",
        body=(
            (
                f"Accès accordé à {_requester_name(req.requester)} "
                f"({_role_label(req.requester)}) pour "
                f"{req.requested_duration_hours} h. Il se fermera automatiquement."
            )
            if req.requested_duration_hours
            else (
                f"Accès durable accordé à {_requester_name(req.requester)} "
                f"({_role_label(req.requester)}). Vous pouvez le révoquer à tout moment."
            )
        ),
        type=Notification.Type.ACCESS_GRANTED,
        payload={
            "access_request_id": req.id,
            "requester_id": req.requester_id,
            "approved": True,
            "confirmation": True,
            "permanent": True,
        },
        push=False,
    )
    return req


def prior_medecin_respond(req: AccessRequest, *, approve: bool, prior_user) -> AccessRequest:
    """Ancien médecin : confirme le transfert après la validation du patient."""
    from rest_framework.exceptions import PermissionDenied, ValidationError

    expire_stale_requests(patient=req.patient)
    req.refresh_from_db()
    if req.status != AccessRequest.Status.PENDING_PRIOR:
        raise ValidationError({"detail": "Cette demande n'attend pas de validation de l'ancien médecin."})
    if req.patient_approved_at is None:
        raise ValidationError({"detail": "Le patient n'a pas encore validé ce transfert."})
    if req.prior_medecin_id != prior_user.id and getattr(prior_user, "role", None) != Roles.ADMIN:
        raise PermissionDenied("Seul l'ancien médecin traitant peut valider ce transfert.")
    if timezone.now() >= req.expires_at:
        expire_stale_requests(patient=req.patient)
        req.refresh_from_db()
        if req.status != AccessRequest.Status.PENDING_PRIOR:
            return req

    now = timezone.now()
    if not approve:
        req.status = AccessRequest.Status.DENIED
        req.responded_at = now
        req.save(update_fields=["status", "responded_at"])
        notify_user(
            req.requester,
            title="Transfert refusé par l'ancien médecin",
            body=f"{_requester_name(prior_user)} a refusé le transfert pour {req.patient.full_name}.",
            type=Notification.Type.ACCESS_DENIED,
            payload={"access_request_id": req.id, "prior_denied": True},
        )
        hub_bus.publish(
            req.requester_id,
            {
                "type": "access_denied",
                "access_request_id": req.id,
                "patient_id": req.patient_id,
                "npi": req.patient.npi,
                "full_name": req.patient.full_name,
                "prior_denied": True,
                "ts": now.isoformat(),
            },
        )
        patient_user = getattr(req.patient, "user", None)
        if patient_user:
            notify_user(
                patient_user,
                title="Transfert refusé par l'ancien médecin",
                body=(
                    f"{_requester_name(prior_user)} a refusé le transfert vers "
                    f"{_requester_name(req.requester)}, malgré votre accord."
                ),
                type=Notification.Type.ACCESS_DENIED,
                payload={"access_request_id": req.id, "prior_denied": True},
                push=False,
            )
        return req

    req.prior_medecin_approved_at = now
    req.save(update_fields=["prior_medecin_approved_at"])
    AuditLog.objects.create(
        user=prior_user,
        username=getattr(prior_user, "username", ""),
        action="access_prior_approved",
        target=f"access:{req.id}",
        patient_npi=req.patient.npi,
    )
    req = _finalize_approved_grant(req, actor=prior_user)
    patient_user = getattr(req.patient, "user", None)
    if patient_user:
        notify_user(
            patient_user,
            title="Transfert confirmé",
            body=(
                f"{_requester_name(prior_user)} a confirmé. "
                f"{_requester_name(req.requester)} peut ouvrir votre dossier."
            ),
            type=Notification.Type.ACCESS_GRANTED,
            payload={"access_request_id": req.id, "approved": True, "confirmation": True},
            push=False,
        )
    return req


def _live_grant_rows(requester, patient):
    return AccessRequest.objects.filter(
        requester=requester,
        patient=patient,
        status__in=(
            AccessRequest.Status.APPROVED,
            AccessRequest.Status.EMERGENCY_BYPASS,
        ),
    ).order_by("-created_at")


def _pick_live_grant(requester, patient):
    """Préfère un accès dossier encore dans les temps. L'urgence ne masque pas un accès complet."""
    from .hopital import has_structure_direct_access

    emergency_hit = None
    for req in _live_grant_rows(requester, patient):
        if req.direct_structure:
            if grant_deadline_passed(req) or not req.has_active_grant:
                continue
            if has_structure_direct_access(requester, patient):
                return req
            continue
        if not req.has_active_grant or grant_deadline_passed(req):
            continue
        if req.status == AccessRequest.Status.EMERGENCY_BYPASS or (req.scope or "") == "urgence":
            emergency_hit = emergency_hit or req
            continue
        return req
    return emergency_hit


def has_active_grant(requester, patient) -> AccessRequest | None:
    from .hopital import ensure_direct_grant, has_structure_direct_access

    expire_stale_requests(patient=patient, requester=requester)
    if is_access_blocked(requester, patient):
        return None
    found = _pick_live_grant(requester, patient)
    if found is not None:
        return found
    if has_structure_direct_access(requester, patient):
        return ensure_direct_grant(requester, patient)
    return None


def can_read_dossier(user, patient) -> bool:
    """Relit la date de fin sans créer de jeton (liste, hub, cache)."""
    from .hopital import bypasses_consent, has_structure_direct_access

    if patient is None:
        return False
    if bypasses_consent(user, patient):
        return True
    expire_stale_requests(patient=patient, requester=user)
    if is_access_blocked(user, patient):
        return False
    if has_structure_direct_access(user, patient):
        return True
    grant = _pick_live_grant(user, patient)
    return grant_allows_full(grant)


def access_expired_for(user, patient) -> bool:
    """Un accès limité a existé et sa date de fin est passée. L'hôpital mère n'est pas concerné."""
    from .hopital import user_structure_id

    if patient is None:
        return False
    sid = user_structure_id(user)
    if patient.hopital_mere_id and sid == patient.hopital_mere_id:
        return False
    from .models import HopitalMereTransfer

    if sid and HopitalMereTransfer.objects.filter(
        patient=patient,
        status__in=(
            HopitalMereTransfer.Status.PENDING,
            HopitalMereTransfer.Status.EN_COURS,
        ),
    ).filter(
        Q(from_structure_id=sid) | Q(to_structure_id=sid)
    ).exists():
        return False
    rows = AccessRequest.objects.filter(
        requester=user,
        patient=patient,
        status__in=(
            AccessRequest.Status.APPROVED,
            AccessRequest.Status.EXPIRED,
            AccessRequest.Status.EMERGENCY_BYPASS,
        ),
    ).order_by("-id")[:30]
    elapsed = False
    for req in rows:
        live_full = (
            req.status == AccessRequest.Status.APPROVED
            and req.has_active_grant
            and not grant_deadline_passed(req)
            and (req.scope or "") != "urgence"
        )
        if live_full:
            if req.direct_structure:
                from .hopital import has_structure_direct_access

                if has_structure_direct_access(user, patient):
                    return False
                continue
            return False
        if req.status == AccessRequest.Status.EXPIRED or grant_deadline_passed(req):
            elapsed = True
    return elapsed


def assert_patient_access(user, patient, *, allow_admin: bool = True) -> AccessRequest | None:
    """Lève PermissionDenied si le pro n'a pas de grant actif (admin exempt)."""
    from rest_framework.exceptions import PermissionDenied

    from core.permissions import Roles

    if allow_admin and getattr(user, "role", None) == Roles.ADMIN:
        from .hopital import bypasses_consent

        if bypasses_consent(user, patient):
            return None
    if getattr(user, "role", None) == Roles.PATIENT:
        own = getattr(user, "patient", None)
        if own and own.pk == patient.pk:
            return None
        raise PermissionDenied("Accès réservé à votre propre dossier.")
    grant = has_active_grant(user, patient)
    if grant_allows_full(grant):
        return grant
    if access_expired_for(user, patient):
        raise PermissionDenied("Cet accès a expiré")
    raise PermissionDenied(
        {
            "detail": "Consentement patient requis.",
            "code": "consent_required",
            "consent_required": True,
            "patient_id": patient.pk,
        }
    )


def grant_allows_full(req: AccessRequest | None) -> bool:
    """Dossier complet : urgence et échéance passée ne comptent pas."""
    if req is None:
        return False
    if req.status == AccessRequest.Status.EMERGENCY_BYPASS or (req.scope or "") == "urgence":
        return False
    from .hopital import grant_is_live

    return grant_is_live(req)


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
    duration_hours: int | None = None,
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

    # Réutiliser un grant encore valide (accès durable, direct, ou à durée limitée).
    existing = has_active_grant(requester, patient)
    if existing and existing.has_active_grant and (
        not emergency or existing.direct_structure
    ):
        existing._reused = True  # type: ignore[attr-defined]
        if existing.direct_structure:
            from .hopital import audit_dossier_open

            audit_dossier_open(requester, patient)
        elif existing.is_access_stale():
            existing._pin_required = True  # type: ignore[attr-defined]
        else:
            existing.last_accessed_at = timezone.now()
            existing.save(update_fields=["last_accessed_at"])
            # Notifier patient même sur réouverture récente ? non — seulement stale
        return existing

    from .hopital import (
        create_hopital_mere_request,
        patient_controls_external_requests,
    )

    # Sans application : l'autre structure s'adresse à l'hôpital mère, avec une durée.
    # L'ambulancier / l'urgence vitale ne passent pas par cette file.
    if (
        not emergency
        and not is_emergency_requester(requester, emergency)
        and not patient_controls_external_requests(patient)
    ):
        return create_hopital_mere_request(
            requester=requester,
            patient=patient,
            mode=mode,
            reason=reason,
            dodocard=dodocard,
            duration_hours=duration_hours,
            request=request,
        )

    # Réutiliser une demande pending encore valide (évite d'invalider
    # la ConsentCard pendant que le patient répond / double requestAccess).
    if not emergency:
        pending = (
            AccessRequest.objects.filter(
                requester=requester,
                patient=patient,
                status__in=(
                    AccessRequest.Status.PENDING,
                    AccessRequest.Status.PENDING_PRIOR,
                ),
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
        status__in=(AccessRequest.Status.PENDING, AccessRequest.Status.PENDING_PRIOR),
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
        from .hopital import notify_mere_of_emergency

        notify_mere_of_emergency(patient, requester, req)
        return req

    # Nouveau médecin : le patient valide d'abord. L'ancien médecin n'est notifié qu'après.
    prior_user = None
    if getattr(requester, "role", None) == Roles.MEDECIN:
        prior_user = resolve_medecin_traitant(patient, excluding_user=requester)

    if prior_user is not None:
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
            prior_medecin=prior_user,
        )
        AuditLog.objects.create(
            user=requester,
            username=requester.username,
            action="access_request_created_awaiting_patient",
            target=f"access:{req.id}",
            patient_npi=patient.npi,
            method=getattr(request, "method", "") if request else "",
            path=getattr(request, "path", "")[:300] if request else "",
        )
        _notify_patient_consent(req)
        if mode != AccessRequest.Mode.SCAN:
            hub_bus.publish(
                requester.id,
                {
                    "type": "access_pending",
                    "access_request_id": req.id,
                    "patient_id": patient.id,
                    "npi": patient.npi,
                    "full_name": patient.full_name,
                    "awaiting_prior": False,
                    "expires_at": req.expires_at.isoformat(),
                    "ts": now.isoformat(),
                },
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

    _notify_patient_consent(req)

    # Pro : état pending. Mode SCAN : ScanView publie déjà `dodocard_scan`
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


def cancel_access_request(req: AccessRequest, *, requester) -> AccessRequest:
    """Pro : annule sa propre demande encore en attente (retire la notif patient)."""
    expire_stale_requests(patient=req.patient, requester=requester)
    req.refresh_from_db()
    if req.requester_id != requester.id and getattr(requester, "role", None) != Roles.ADMIN:
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("Vous ne pouvez annuler que vos propres demandes.")
    if req.status not in (AccessRequest.Status.PENDING, AccessRequest.Status.PENDING_PRIOR):
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

    from .hopital import duration_label, grant_is_live

    data = {
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
        "patient_approved_at": req.patient_approved_at.isoformat() if req.patient_approved_at else None,
        "prior_medecin_id": req.prior_medecin_id,
        "prior_medecin_name": _requester_name(req.prior_medecin) if req.prior_medecin_id else None,
        "prior_medecin_approved_at": (
            req.prior_medecin_approved_at.isoformat() if req.prior_medecin_approved_at else None
        ),
        "grant_expires_at": req.grant_expires_at.isoformat() if req.grant_expires_at else None,
        "last_accessed_at": req.last_accessed_at.isoformat() if req.last_accessed_at else None,
        "route": req.route,
        "requested_duration_hours": req.requested_duration_hours,
        "duration_label": duration_label(req.requested_duration_hours),
        "direct_structure": req.direct_structure,
        "decision_structure": (
            req.decision_structure.nom if getattr(req, "decision_structure_id", None) and req.decision_structure_id else None
        ),
        "has_active_grant": grant_is_live(req) if req.direct_structure else req.has_active_grant,
        "permanent": req.status == AccessRequest.Status.APPROVED and req.grant_expires_at is None,
        "pin_required": (not req.direct_structure) and (
            bool(getattr(req, "_pin_required", False))
            or (req.has_active_grant and req.is_access_stale())
        ),
        "awaiting_prior": req.status == AccessRequest.Status.PENDING_PRIOR,
        "awaiting_hopital_mere": (
            req.status == AccessRequest.Status.PENDING
            and req.route == AccessRequest.Route.HOPITAL_MERE
        ),
    }
    return data


def activate_patient_application(patient, user) -> int:
    """Relie un dossier créé par l'hôpital à un téléphone.

    Les demandes extérieures encore en attente à l'hôpital mère reviennent au patient.
    L'hôpital mère garde l'accès direct de ses professionnels.
    """
    now = timezone.now()
    patient.user = user
    patient.app_activee_le = now
    fields = ["user", "app_activee_le", "updated_at"]
    phone = (getattr(user, "telephone", "") or "").strip()
    if phone and not (patient.telephone or "").strip():
        patient.telephone = phone
        fields.append("telephone")
    patient.save(update_fields=fields)

    from .hopital import desk_users

    pending = AccessRequest.objects.filter(
        patient=patient,
        status=AccessRequest.Status.PENDING,
        route=AccessRequest.Route.HOPITAL_MERE,
    ).select_related("requester", "patient", "structure")
    count = 0
    for req in pending:
        req.route = AccessRequest.Route.PATIENT
        req.save(update_fields=["route"])
        _notify_patient_consent(req)
        notify_user(
            req.requester,
            title="Le patient valide désormais",
            body=(
                f"{patient.full_name} a activé son application. "
                "C'est lui qui accepte ou refuse votre demande."
            ),
            type=Notification.Type.SYSTEM,
            payload={"access_request_id": req.id, "patient_id": patient.id, "route": "patient"},
        )
        count += 1
    if patient.hopital_mere_id:
        for desk in desk_users(patient.hopital_mere_id):
            notify_user(
                desk,
                title="Le patient gère ses accès",
                body=(
                    f"{patient.full_name} a activé son téléphone. "
                    "Les demandes extérieures ne passent plus par l'accueil. "
                    "Les professionnels de l'hôpital mère gardent l'accès direct."
                ),
                type=Notification.Type.SYSTEM,
                payload={"patient_id": patient.id, "app_activee": True},
                push=False,
            )
    AuditLog.objects.create(
        user=user,
        username=getattr(user, "username", ""),
        action="patient_application_activee",
        target=f"patient:{patient.id}|demandes:{count}",
        patient_npi=patient.npi,
    )
    return count
