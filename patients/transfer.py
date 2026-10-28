"""Transfert d'hôpital mère : la demande part du nouvel hôpital, l'ancien accepte."""
from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditLog
from notifications.models import Notification
from notifications.services import notify_user

from .hopital import DESK_ROLES, desk_users, duration_label, parse_duration_hours, user_structure_id
from .models import HopitalMereTransfer, Patient


def _name(user) -> str:
    if user is None:
        return ""
    return (user.get_full_name() or user.username or "").strip()


def _is_desk(user) -> bool:
    return getattr(user, "role", None) in DESK_ROLES and bool(user_structure_id(user))


def _notify_desk(structure_id, *, title: str, body: str, payload: dict) -> None:
    for user in desk_users(structure_id):
        notify_user(
            user,
            title=title,
            body=body,
            type=Notification.Type.SYSTEM,
            payload=payload,
        )


def serialize_transfer(obj: HopitalMereTransfer) -> dict:
    return {
        "id": obj.id,
        "patient_id": obj.patient_id,
        "patient_name": obj.patient.full_name,
        "patient_npi": obj.patient.npi,
        "from_structure_id": obj.from_structure_id,
        "from_structure_nom": obj.from_structure.nom,
        "to_structure_id": obj.to_structure_id,
        "to_structure_nom": obj.to_structure.nom,
        "requested_by_name": _name(obj.requested_by),
        "duration_hours": obj.duration_hours,
        "duration_label": duration_label(obj.duration_hours),
        "status": obj.status,
        "status_label": obj.get_status_display(),
        "from_approved_at": obj.from_approved_at.isoformat() if obj.from_approved_at else None,
        "to_approved_at": obj.to_approved_at.isoformat() if obj.to_approved_at else None,
        "confirmed_at": obj.confirmed_at.isoformat() if obj.confirmed_at else None,
        "legacy_access_until": (
            obj.legacy_access_until.isoformat() if obj.legacy_access_until else None
        ),
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "peut_accepter": None,
        "peut_confirmer": obj.status == HopitalMereTransfer.Status.EN_COURS,
    }


def serialize_transfer_for(obj: HopitalMereTransfer, user) -> dict:
    data = serialize_transfer(obj)
    sid = user_structure_id(user)
    data["peut_confirmer"] = (
        obj.status == HopitalMereTransfer.Status.EN_COURS
        and _is_desk(user)
        and sid in (obj.from_structure_id, obj.to_structure_id)
    )
    needs_from = obj.from_approved_at is None and sid == obj.from_structure_id
    needs_to = obj.to_approved_at is None and sid == obj.to_structure_id
    data["peut_accepter"] = (
        obj.status == HopitalMereTransfer.Status.PENDING
        and _is_desk(user)
        and (needs_from or needs_to)
    )
    data["peut_annuler"] = obj.status in (
        HopitalMereTransfer.Status.PENDING,
        HopitalMereTransfer.Status.EN_COURS,
    ) and _is_desk(user) and sid in (obj.from_structure_id, obj.to_structure_id)
    return data


def inbox_transfers(user):
    sid = user_structure_id(user)
    if not _is_desk(user) or not sid:
        return HopitalMereTransfer.objects.none()
    return (
        HopitalMereTransfer.objects.filter(Q(from_structure_id=sid) | Q(to_structure_id=sid))
        .exclude(
            status__in=(
                HopitalMereTransfer.Status.REFUSE,
                HopitalMereTransfer.Status.ANNULE,
            )
        )
        .select_related("patient", "from_structure", "to_structure", "requested_by")
        .order_by("-created_at")
    )


@transaction.atomic
def initiate_transfer(*, actor, patient: Patient, to_structure_id: int | None, duration_hours: int | None):
    if not _is_desk(actor):
        raise PermissionDenied("Seuls l'accueil et l'admin de la structure peuvent transférer.")
    if not patient.hopital_mere_id:
        raise ValidationError({"detail": "Ce patient n'a pas d'hôpital mère."})
    if not duration_hours:
        raise ValidationError({"detail": "Indiquez la durée pendant laquelle l'ancien hôpital garde l'accès."})
    sid = user_structure_id(actor)
    if sid == patient.hopital_mere_id:
        raise PermissionDenied(
            "La demande part du nouvel hôpital, pas de l'hôpital mère actuel. "
            "L'ancien hôpital doit ensuite accepter."
        )
    if to_structure_id in (None, ""):
        to_structure_id = sid
    else:
        try:
            to_structure_id = int(to_structure_id)
        except (TypeError, ValueError):
            raise ValidationError({"detail": "Structure de destination invalide."})
    if to_structure_id != sid:
        raise ValidationError(
            {
                "detail": (
                    "Vous ne pouvez demander le transfert que pour votre hôpital, "
                    "celui qui veut devenir l'hôpital mère."
                )
            }
        )
    if to_structure_id == patient.hopital_mere_id:
        raise ValidationError({"detail": "Cette structure est déjà l'hôpital mère."})
    from accounts.models import StructureSante

    dest = StructureSante.objects.filter(pk=to_structure_id).first()
    if dest is None:
        raise ValidationError({"detail": "Structure de destination introuvable."})
    if HopitalMereTransfer.objects.filter(
        patient=patient,
        status__in=(
            HopitalMereTransfer.Status.PENDING,
            HopitalMereTransfer.Status.EN_COURS,
        ),
    ).exists():
        raise ValidationError({"detail": "Un transfert est déjà en cours pour ce patient."})

    now = timezone.now()
    transfer = HopitalMereTransfer(
        patient=patient,
        from_structure_id=patient.hopital_mere_id,
        to_structure=dest,
        requested_by=actor,
        duration_hours=duration_hours,
        status=HopitalMereTransfer.Status.PENDING,
        to_approved_by=actor,
        to_approved_at=now,
    )
    other_id = patient.hopital_mere_id
    other_label = patient.hopital_mere.nom
    transfer.save()
    AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", ""),
        action="hopital_mere_transfert_demande",
        target=f"transfer:{transfer.id}|{patient.hopital_mere_id}->{dest.id}",
        patient_npi=patient.npi,
    )
    _notify_desk(
        other_id,
        title="Transfert d'hôpital mère",
        body=(
            f"{dest.nom} demande à devenir l'hôpital mère de {patient.full_name}. "
            f"L'accord de {other_label} est requis. Pendant le transfert, les deux ont accès. "
            f"Une fois confirmé, {dest.nom} reçoit les demandes extérieures. "
            f"L'ancien hôpital garderait l'accès déjà ouvert pendant {duration_label(duration_hours)}."
        ),
        payload={"transfer_id": transfer.id, "patient_id": patient.id, "kind": "hopital_mere_transfer"},
    )
    return transfer


def _both_agreed(transfer: HopitalMereTransfer) -> bool:
    return bool(transfer.from_approved_at and transfer.to_approved_at)


@transaction.atomic
def respond_transfer(transfer: HopitalMereTransfer, *, approve: bool, actor) -> HopitalMereTransfer:
    if not _is_desk(actor):
        raise PermissionDenied("Seuls l'accueil et l'admin de la structure peuvent répondre.")
    sid = user_structure_id(actor)
    transfer = (
        HopitalMereTransfer.objects.select_for_update()
        .select_related("patient", "from_structure", "to_structure")
        .get(pk=transfer.pk)
    )
    if transfer.status not in (
        HopitalMereTransfer.Status.PENDING,
        HopitalMereTransfer.Status.EN_COURS,
    ):
        raise ValidationError({"detail": "Ce transfert n'est plus ouvert."})
    if sid not in (transfer.from_structure_id, transfer.to_structure_id):
        raise PermissionDenied("Votre structure n'est pas partie à ce transfert.")

    now = timezone.now()
    if not approve:
        transfer.status = HopitalMereTransfer.Status.REFUSE
        transfer.save(update_fields=["status", "updated_at"])
        AuditLog.objects.create(
            user=actor,
            username=getattr(actor, "username", ""),
            action="hopital_mere_transfert_refuse",
            target=f"transfer:{transfer.id}",
            patient_npi=transfer.patient.npi,
        )
        for structure_id in (transfer.from_structure_id, transfer.to_structure_id):
            _notify_desk(
                structure_id,
                title="Transfert refusé",
                body=f"Le transfert de {transfer.patient.full_name} a été refusé.",
                payload={"transfer_id": transfer.id, "patient_id": transfer.patient_id},
            )
        return transfer

    if transfer.status != HopitalMereTransfer.Status.PENDING:
        raise ValidationError({"detail": "Les deux hôpitaux ont déjà accepté. Confirmez le transfert."})

    if sid == transfer.from_structure_id and transfer.from_approved_at is None:
        transfer.from_approved_by = actor
        transfer.from_approved_at = now
    elif sid == transfer.to_structure_id and transfer.to_approved_at is None:
        transfer.to_approved_by = actor
        transfer.to_approved_at = now
    else:
        raise ValidationError({"detail": "Votre hôpital a déjà répondu."})

    if _both_agreed(transfer):
        transfer.status = HopitalMereTransfer.Status.EN_COURS
    transfer.save()
    AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", ""),
        action="hopital_mere_transfert_accepte",
        target=f"transfer:{transfer.id}|{transfer.status}",
        patient_npi=transfer.patient.npi,
    )
    if transfer.status == HopitalMereTransfer.Status.EN_COURS:
        for structure_id in (transfer.from_structure_id, transfer.to_structure_id):
            _notify_desk(
                structure_id,
                title="Transfert en cours",
                body=(
                    f"Les deux hôpitaux ont accepté pour {transfer.patient.full_name}. "
                    "Les deux ont accès au dossier jusqu'à la confirmation."
                ),
                payload={"transfer_id": transfer.id, "patient_id": transfer.patient_id},
            )
    return transfer


@transaction.atomic
def confirm_transfer(transfer: HopitalMereTransfer, *, actor) -> HopitalMereTransfer:
    if not _is_desk(actor):
        raise PermissionDenied("Seuls l'accueil et l'admin de la structure peuvent confirmer.")
    sid = user_structure_id(actor)
    transfer = (
        HopitalMereTransfer.objects.select_for_update()
        .select_related("patient", "from_structure", "to_structure")
        .get(pk=transfer.pk)
    )
    if transfer.status != HopitalMereTransfer.Status.EN_COURS:
        raise ValidationError({"detail": "Les deux hôpitaux doivent avoir accepté avant la confirmation."})
    if sid not in (transfer.from_structure_id, transfer.to_structure_id):
        raise PermissionDenied("Votre structure n'est pas partie à ce transfert.")
    now = timezone.now()
    transfer.status = HopitalMereTransfer.Status.CONFIRME
    transfer.confirmed_by = actor
    transfer.confirmed_at = now
    transfer.legacy_access_until = now + timezone.timedelta(hours=int(transfer.duration_hours))
    transfer.save()
    patient = transfer.patient
    patient.hopital_mere_id = transfer.to_structure_id
    patient.save(update_fields=["hopital_mere", "updated_at"])
    AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", ""),
        action="hopital_mere_transfert_confirme",
        target=(
            f"transfer:{transfer.id}|{transfer.from_structure_id}->{transfer.to_structure_id}"
            f"|jusqu'a:{transfer.legacy_access_until.isoformat()}"
        )[:255],
        patient_npi=patient.npi,
    )
    label = duration_label(transfer.duration_hours)
    body = (
        f"{transfer.to_structure.nom} est le nouvel hôpital mère de {patient.full_name}. "
        f"Les nouvelles demandes lui sont adressées. "
        f"{transfer.from_structure.nom} garde l'accès déjà ouvert jusqu'au "
        f"{transfer.legacy_access_until:%d/%m/%Y %H:%M} ({label})."
    )
    for structure_id in (transfer.from_structure_id, transfer.to_structure_id):
        _notify_desk(
            structure_id,
            title="Hôpital mère transféré",
            body=body,
            payload={"transfer_id": transfer.id, "patient_id": patient.id},
        )
    return transfer


@transaction.atomic
def cancel_transfer(transfer: HopitalMereTransfer, *, actor) -> HopitalMereTransfer:
    if not _is_desk(actor):
        raise PermissionDenied("Seuls l'accueil et l'admin de la structure peuvent annuler.")
    sid = user_structure_id(actor)
    if transfer.status not in (
        HopitalMereTransfer.Status.PENDING,
        HopitalMereTransfer.Status.EN_COURS,
    ):
        raise ValidationError({"detail": "Ce transfert ne peut plus être annulé."})
    if sid not in (transfer.from_structure_id, transfer.to_structure_id):
        raise PermissionDenied("Votre structure n'est pas partie à ce transfert.")
    transfer.status = HopitalMereTransfer.Status.ANNULE
    transfer.save(update_fields=["status", "updated_at"])
    AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", ""),
        action="hopital_mere_transfert_annule",
        target=f"transfer:{transfer.id}",
        patient_npi=transfer.patient.npi,
    )
    return transfer


def duration_from_request(data) -> int | None:
    return parse_duration_hours(data)
