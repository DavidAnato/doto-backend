"""Création notification + SSE + push."""
from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from cards.pubsub import hub_bus

from .models import DeviceToken, Notification
from .push import send_expo_push

User = get_user_model()


def notify_user(
    user,
    *,
    title: str,
    body: str = "",
    type: str = Notification.Type.SYSTEM,
    payload: dict[str, Any] | None = None,
    push: bool = True,
) -> Notification:
    """Crée une notif in-app, publie SSE sur le canal user, optionnellement push."""
    notif = Notification.objects.create(
        user=user,
        title=title[:160],
        body=body or "",
        type=type,
        payload=payload or {},
    )
    event = {
        "type": "notification",
        "notification_id": notif.id,
        "notif_type": notif.type,
        "title": notif.title,
        "body": notif.body,
        "payload": notif.payload,
        "ts": timezone.now().isoformat(),
    }
    # Canal unifié par user_id (pro hub SSE + patient SSE)
    hub_bus.publish(user.id, event)

    if push:
        tokens = list(
            DeviceToken.objects.filter(user=user, enabled=True).values_list("token", flat=True)
        )
        if tokens:
            send_expo_push(
                list(tokens),
                title=notif.title,
                body=notif.body,
                data={
                    "notification_id": notif.id,
                    "type": notif.type,
                    **(notif.payload or {}),
                },
            )
            DeviceToken.objects.filter(user=user, enabled=True).update(
                last_used_at=timezone.now()
            )
    return notif


def notify_admins(
    *,
    title: str,
    body: str = "",
    type: str = Notification.Type.SYSTEM,
    payload: dict[str, Any] | None = None,
) -> int:
    from core.permissions import Roles

    count = 0
    for u in User.objects.filter(role=Roles.ADMIN, is_active=True):
        notify_user(u, title=title, body=body, type=type, payload=payload)
        count += 1
    return count


def notify_patient_dossier_change(
    patient,
    *,
    title: str,
    body: str = "",
    notif_type: str = Notification.Type.DOSSIER_UPDATED,
    event_type: str | None = None,
    section: str | None = None,
    payload: dict[str, Any] | None = None,
    actor=None,
) -> Notification | None:
    """Notifie le patient (in-app + SSE + push) et diffuse un event typé aux sessions pro.

    event_type SSE (canal patient + acteur) :
      dossier_updated | ordonnance | examen | appointment | insurance_updated
    section payload (badges Mon dossier) :
      dossier | ordonnances | examens | assurance | rdv
    """
    ev = event_type or notif_type
    section_key = section or {
        Notification.Type.ORDONNANCE: "ordonnances",
        Notification.Type.EXAMEN: "examens",
        Notification.Type.DOSSIER_UPDATED: "dossier",
    }.get(notif_type, "dossier")

    data: dict[str, Any] = {
        **(payload or {}),
        "patient_id": getattr(patient, "pk", None) or getattr(patient, "id", None),
        "section": section_key,
    }

    notif: Notification | None = None
    user = getattr(patient, "user", None)
    if user is not None:
        notif = notify_user(
            user,
            title=title,
            body=body,
            type=notif_type,
            payload=data,
        )
        # Event typé en plus de « notification » — invalidations ciblées côté client
        hub_bus.publish(
            user.id,
            {
                "type": ev,
                "patient_id": data["patient_id"],
                "notif_type": notif_type,
                "title": title[:160],
                "body": body or "",
                "payload": data,
                "ts": timezone.now().isoformat(),
            },
        )

    actor_id = getattr(actor, "id", None)
    if actor_id and (user is None or actor_id != user.id):
        hub_bus.publish(
            actor_id,
            {
                "type": ev,
                "patient_id": data["patient_id"],
                "notif_type": notif_type,
                "payload": data,
                "ts": timezone.now().isoformat(),
            },
        )
    return notif


def publish_professionals(event: dict[str, Any], *, structure_id=None) -> int:
    """Diffuse un event SSE à tous les professionnels (optionnellement d'une structure)."""
    from django.db.models import Q

    from core.permissions import Roles

    qs = User.objects.filter(role__in=Roles.PROFESSIONALS, actif=True)
    if structure_id:
        qs = qs.filter(
            Q(structure_principale_id=structure_id) | Q(structures__id=structure_id)
        ).distinct()
    count = 0
    for uid in qs.values_list("id", flat=True):
        hub_bus.publish(uid, event)
        count += 1
    return count


def _publish_user_ids(event: dict[str, Any], user_ids) -> None:
    seen: set[int] = set()
    for uid in user_ids:
        if not uid or uid in seen:
            continue
        seen.add(int(uid))
        hub_bus.publish(int(uid), event)


def _admin_user_ids():
    from core.permissions import Roles

    return list(User.objects.filter(role=Roles.ADMIN, actif=True).values_list("id", flat=True))


def publish_patient_list(*, patient_id, actor=None, kind="updated"):
    event = {
        "type": "patient_list",
        "patient_id": patient_id,
        "kind": kind,
        "ts": timezone.now().isoformat(),
    }
    structure_id = getattr(actor, "structure_principale_id", None) if actor else None
    publish_professionals(event, structure_id=structure_id)
    extra = [getattr(actor, "id", None), *_admin_user_ids()]
    _publish_user_ids(event, extra)
    return event


def appointment_sse_payload(appt, *, kind: str) -> dict[str, Any]:
    pro = getattr(appt, "professionnel", None)
    pro_nom = ""
    if pro is not None:
        pro_nom = pro.get_full_name() or getattr(pro, "username", "") or ""
    debut = getattr(appt, "debut", None)
    fin = getattr(appt, "fin", None)
    return {
        "patient_id": appt.patient_id,
        "appointment_id": appt.id,
        "debut": debut.isoformat() if debut else None,
        "fin": fin.isoformat() if fin else None,
        "statut": appt.statut,
        "professionnel_id": appt.professionnel_id,
        "professionnel_nom": pro_nom,
        "structure_id": appt.structure_id,
        "kind": kind,
        "section": "rdv",
    }


def publish_appointment(
    appt,
    *,
    actor=None,
    kind="updated",
    title: str = "",
    body: str = "",
    notify_patient: bool = True,
):
    """SSE RDV (patient + pros structure + médecin assigné + admins) + notif patient optionnelle."""
    data = appointment_sse_payload(appt, kind=kind)
    event = {
        "type": "appointment",
        **data,
        "payload": data,
        "ts": timezone.now().isoformat(),
    }
    structure_id = appt.structure_id or (
        getattr(actor, "structure_principale_id", None) if actor else None
    )
    publish_professionals(event, structure_id=structure_id)
    extra = [
        getattr(actor, "id", None),
        appt.professionnel_id,
        appt.created_by_id,
        *_admin_user_ids(),
    ]
    _publish_user_ids(event, extra)

    patient = appt.patient
    if notify_patient and title:
        notify_patient_dossier_change(
            patient,
            title=title,
            body=body,
            notif_type=Notification.Type.APPOINTMENT,
            event_type="appointment",
            section="rdv",
            payload=data,
            actor=actor,
        )
    else:
        user = getattr(patient, "user", None)
        if user is not None:
            hub_bus.publish(user.id, event)
    return event


def insurance_sse_payload(patient, *, kind: str, assurance=None) -> dict[str, Any]:
    inst = assurance if kind != "removed" else None
    if inst is None and kind != "removed":
        inst = getattr(patient, "assurance", None)
    has = bool(
        inst
        and getattr(inst, "assureur", None)
        and getattr(inst, "droits_valides", True)
    )
    return {
        "patient_id": getattr(patient, "pk", None) or getattr(patient, "id", None),
        "kind": kind,
        "section": "assurance",
        "has_insurance": has,
        "assureur": (getattr(inst, "assureur", None) or "") if inst else "",
        "num_police": (getattr(inst, "num_police", None) or "") if inst else "",
        "droits_valides": bool(getattr(inst, "droits_valides", False)) if inst else False,
        "assurance_id": getattr(inst, "id", None) if inst else None,
    }


def publish_insurance_updated(
    patient,
    *,
    actor=None,
    kind="updated",
    assurance=None,
    notify_patient: bool = True,
):
    """SSE assurance (ajout / MAJ / retrait) — patient + pros + admins."""
    data = insurance_sse_payload(patient, kind=kind, assurance=assurance)
    event = {
        "type": "insurance_updated",
        **data,
        "payload": data,
        "ts": timezone.now().isoformat(),
    }
    structure_id = getattr(actor, "structure_principale_id", None) if actor else None
    publish_professionals(event, structure_id=structure_id)
    extra = [getattr(actor, "id", None), *_admin_user_ids()]
    _publish_user_ids(event, extra)

    titles = {
        "created": "Assurance enregistrée",
        "updated": "Assurance mise à jour",
        "removed": "Assurance retirée",
    }
    bodies = {
        "created": "Votre couverture assurantielle a été enregistrée.",
        "updated": "Votre couverture assurantielle a été mise à jour.",
        "removed": "Votre couverture assurantielle a été retirée. La carte affiche Non assuré.",
    }
    if notify_patient:
        notify_patient_dossier_change(
            patient,
            title=titles.get(kind, "Assurance mise à jour"),
            body=bodies.get(kind, ""),
            notif_type=Notification.Type.DOSSIER_UPDATED,
            event_type="insurance_updated",
            section="assurance",
            payload=data,
            actor=actor,
        )
    else:
        user = getattr(patient, "user", None)
        if user is not None:
            hub_bus.publish(user.id, event)
    publish_patient_list(patient_id=data["patient_id"], actor=actor, kind="updated")
    return event
