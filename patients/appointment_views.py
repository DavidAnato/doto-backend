"""API rendez-vous (RDV)."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.utils import log_action
from core.permissions import Roles, role_can, role_can_write
from notifications.models import Notification
from notifications.services import notify_patient_dossier_change, notify_user, publish_appointment

from .models import Appointment
from .serializers import AppointmentSerializer

User = get_user_model()


def _reload_appt(pk):
    return Appointment.objects.select_related(
        "patient", "structure", "professionnel", "created_by"
    ).get(pk=pk)


class AppointmentViewSet(viewsets.ModelViewSet):
    """
    Patient : ses RDV (lecture + annulation uniquement - pas de création).
    Médecin / réceptionniste / admin : agenda structure + création (role_can_write).
    Infirmier : lecture agenda si section rdv.

    Réception :
    - avec médecin → statut planifié, notif médecin (à confirmer)
    - sans médecin → RDV guichet (confirmé, professionnel vide)
    """

    serializer_class = AppointmentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "structure", "professionnel", "statut"]

    def get_queryset(self):
        qs = Appointment.objects.select_related(
            "patient", "structure", "professionnel", "created_by"
        ).all()
        user = self.request.user
        if user.role == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            return qs.filter(patient=patient) if patient else qs.none()
        if user.role == Roles.ADMIN:
            qs = qs
        else:
            structure = getattr(user, "structure_principale", None)
            if structure:
                qs = (qs.filter(structure=structure) | qs.filter(professionnel=user)).distinct()
            else:
                qs = qs.filter(professionnel=user)

        date_exact = self.request.query_params.get("date")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_exact:
            d = parse_date(date_exact)
            if d:
                qs = qs.filter(debut__date=d)
        else:
            if date_from:
                d0 = parse_date(date_from) or parse_datetime(date_from)
                if isinstance(d0, datetime):
                    qs = qs.filter(debut__gte=d0)
                elif d0:
                    qs = qs.filter(debut__date__gte=d0)
            if date_to:
                d1 = parse_date(date_to) or parse_datetime(date_to)
                if isinstance(d1, datetime):
                    qs = qs.filter(debut__lte=d1)
                elif d1:
                    qs = qs.filter(debut__date__lte=d1)
        return qs

    def list(self, request, *args, **kwargs):
        user = request.user
        if user.role not in (Roles.PATIENT, Roles.ADMIN) and not role_can(user.role, "rdv"):
            raise PermissionDenied("Agenda non accessible pour ce rôle.")
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="medecins")
    def medecins(self, request):
        """Médecins de la structure (pour prise de RDV réception)."""
        user = request.user
        if user.role == Roles.PATIENT:
            raise PermissionDenied()
        if not role_can(user.role, "rdv") and user.role != Roles.ADMIN:
            raise PermissionDenied("Liste médecins non accessible.")
        qs = User.objects.filter(role=Roles.MEDECIN, actif=True).order_by(
            "last_name", "first_name"
        )
        if user.role != Roles.ADMIN:
            structure = getattr(user, "structure_principale", None)
            struct_ids = set(user.structures.values_list("id", flat=True))
            if structure:
                struct_ids.add(structure.id)
            if struct_ids:
                qs = qs.filter(
                    Q(structure_principale_id__in=struct_ids) | Q(structures__id__in=struct_ids)
                ).distinct()
        return Response(
            [
                {
                    "id": u.id,
                    "full_name": u.get_full_name() or u.username,
                    "username": u.username,
                }
                for u in qs[:100]
            ]
        )

    def create(self, request, *args, **kwargs):
        user = request.user
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)

        if user.role == Roles.PATIENT:
            raise PermissionDenied("Un patient ne peut pas créer de rendez-vous.")
        if not role_can_write(user.role, "rdv"):
            raise PermissionDenied("Création de RDV non autorisée pour ce rôle.")
        if not data.get("patient"):
            return Response(
                {"detail": "patient requis."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not data.get("debut"):
            return Response(
                {"detail": "debut (date/heure) requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not data.get("structure"):
            structure = getattr(user, "structure_principale", None)
            if structure:
                data["structure"] = structure.pk

        professionnel_id = data.get("professionnel") or None
        if professionnel_id in ("", "null", "none"):
            professionnel_id = None
            data.pop("professionnel", None)

        if user.role == Roles.MEDECIN:
            if not professionnel_id:
                data["professionnel"] = user.pk
            data.setdefault("statut", Appointment.Statut.CONFIRME)
        elif user.role == Roles.RECEPTIONNISTE:
            if professionnel_id:
                data["statut"] = Appointment.Statut.PLANIFIE
            else:
                data.pop("professionnel", None)
                data["statut"] = Appointment.Statut.CONFIRME
                notes = (data.get("notes") or "").strip()
                tag = "RDV réception (sans médecin)"
                data["notes"] = f"{tag}. {notes}".strip() if notes else tag
        else:
            if professionnel_id:
                data.setdefault("statut", Appointment.Statut.PLANIFIE)
            else:
                data.setdefault("statut", Appointment.Statut.CONFIRME)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        appt = serializer.save(created_by=user)
        appt = _reload_appt(appt.pk)
        log_action(
            request,
            "creer_rdv",
            target=f"rdv:{appt.id}",
            patient_npi=appt.patient.npi,
        )

        if (
            appt.professionnel_id
            and appt.professionnel_id != user.id
            and appt.statut == Appointment.Statut.PLANIFIE
        ):
            when = appt.debut.strftime("%d/%m/%Y %H:%M")
            notify_user(
                appt.professionnel,
                title="Nouveau RDV à confirmer",
                body=(
                    f"{user.get_full_name() or user.username} a planifié un RDV "
                    f"avec {appt.patient.full_name} le {when}"
                    + (f" - {appt.motif}" if appt.motif else "")
                    + ". Confirmez ou refusez dans l'agenda."
                ),
                type=Notification.Type.APPOINTMENT,
                payload={
                    "kind": "rdv_pending",
                    "appointment_id": appt.id,
                    "patient_id": appt.patient_id,
                    "section": "rdv",
                },
            )

        when = appt.debut.strftime("%d/%m/%Y %H:%M")
        struct = getattr(appt.structure, "nom", None) or "votre structure"
        publish_appointment(
            appt,
            actor=user,
            kind="rdv_created",
            title="Nouveau rendez-vous",
            body=f"RDV prévu le {when} - {struct}"
            + (f" ({appt.motif})" if appt.motif else "")
            + ".",
        )

        return Response(self.get_serializer(appt).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def confirmer(self, request, pk=None):
        """Médecin (ou admin) : confirmer un RDV planifié lui étant assigné."""
        appt = self.get_object()
        user = request.user
        if user.role == Roles.PATIENT:
            raise PermissionDenied()
        if user.role == Roles.MEDECIN and appt.professionnel_id != user.id:
            raise PermissionDenied("Ce RDV ne vous est pas assigné.")
        if user.role not in (Roles.MEDECIN, Roles.ADMIN):
            raise PermissionDenied("Seul le médecin peut confirmer.")
        if appt.statut == Appointment.Statut.ANNULE:
            return Response(
                {"detail": "RDV déjà annulé."}, status=status.HTTP_400_BAD_REQUEST
            )
        appt.statut = Appointment.Statut.CONFIRME
        appt.save(update_fields=["statut", "updated_at"])
        log_action(
            request,
            "confirmer_rdv",
            target=f"rdv:{appt.id}",
            patient_npi=appt.patient.npi,
        )
        if appt.created_by_id and appt.created_by_id != user.id:
            notify_user(
                appt.created_by,
                title="RDV confirmé",
                body=(
                    f"Dr {user.get_full_name() or user.username} a confirmé le RDV "
                    f"de {appt.patient.full_name}."
                ),
                type=Notification.Type.APPOINTMENT,
                payload={
                    "kind": "rdv_confirmed",
                    "appointment_id": appt.id,
                    "patient_id": appt.patient_id,
                    "section": "rdv",
                },
            )
        when = appt.debut.strftime("%d/%m/%Y %H:%M")
        appt = _reload_appt(appt.pk)
        publish_appointment(
            appt,
            actor=user,
            kind="rdv_confirmed",
            title="Rendez-vous confirmé",
            body=f"Votre RDV du {when} a été confirmé.",
        )
        return Response(self.get_serializer(appt).data)

    @action(detail=True, methods=["post"], url_path="demarrer-consultation")
    def demarrer_consultation(self, request, pk=None):
        """Médecin : démarre une consultation depuis un RDV confirmé (walk-in = POST consultations sans appointment)."""
        from django.utils import timezone

        from medical.models import Consultation
        from medical.serializers import ConsultationSerializer

        appt = self.get_object()
        user = request.user
        if user.role not in (Roles.MEDECIN, Roles.ADMIN):
            raise PermissionDenied("Réservé au médecin.")
        if user.role == Roles.MEDECIN and appt.professionnel_id not in (None, user.id):
            raise PermissionDenied("Ce RDV ne vous est pas assigné.")
        if appt.statut not in (Appointment.Statut.CONFIRME, Appointment.Statut.PLANIFIE):
            return Response(
                {"detail": "Seuls les RDV confirmés (ou planifiés) peuvent démarrer une consultation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        existing = Consultation.objects.filter(appointment=appt, annule=False).first()
        if existing:
            return Response(ConsultationSerializer(existing).data)
        specialite = (request.data.get("specialite") or getattr(user, "specialite", "") or "").strip()
        consultation = Consultation.objects.create(
            patient=appt.patient,
            structure=appt.structure or getattr(user, "structure_principale", None),
            medecin=user,
            appointment=appt,
            date=timezone.now(),
            type=Consultation.Type.CONSULTATION,
            specialite=specialite,
            motif=appt.motif or "",
            diagnostic="",
            notes="",
        )
        appt.statut = Appointment.Statut.TERMINE
        appt.save(update_fields=["statut", "updated_at"])
        log_action(
            request,
            "demarrer_consultation",
            target=f"rdv:{appt.id}",
            patient_npi=appt.patient.npi,
        )
        publish_appointment(
            _reload_appt(appt.pk),
            actor=user,
            kind="rdv_termine",
            notify_patient=False,
        )
        notify_patient_dossier_change(
            appt.patient,
            title="Consultation démarrée",
            body="Votre rendez-vous a été transformé en consultation.",
            notif_type=Notification.Type.DOSSIER_UPDATED,
            event_type="dossier_updated",
            section="dossier",
            payload={
                "kind": "consultation",
                "consultation_id": consultation.id,
                "appointment_id": appt.id,
            },
            actor=user,
        )
        return Response(
            ConsultationSerializer(consultation).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        appt = self.get_object()
        user = request.user
        if user.role == Roles.PATIENT:
            statut = request.data.get("statut")
            if statut != Appointment.Statut.ANNULE:
                raise PermissionDenied("Le patient ne peut qu'annuler un rendez-vous.")
            appt.statut = Appointment.Statut.ANNULE
            appt.save(update_fields=["statut", "updated_at"])
            log_action(
                request, "annuler_rdv", target=f"rdv:{appt.id}", patient_npi=appt.patient.npi
            )
            appt = _reload_appt(appt.pk)
            publish_appointment(
                appt,
                actor=user,
                kind="rdv_annule",
                notify_patient=False,
            )
            return Response(self.get_serializer(appt).data)
        if not role_can_write(user.role, "rdv"):
            raise PermissionDenied("Modification RDV non autorisée.")
        if user.role == Roles.MEDECIN and appt.professionnel_id not in (None, user.id):
            if request.data.get("statut") in (
                Appointment.Statut.CONFIRME,
                Appointment.Statut.ANNULE,
            ):
                raise PermissionDenied("Ce RDV ne vous est pas assigné.")
        response = super().partial_update(request, *args, **kwargs)
        new_statut = request.data.get("statut")
        if new_statut:
            log_action(
                request,
                f"rdv_statut_{new_statut}",
                target=f"rdv:{appt.id}",
                patient_npi=appt.patient.npi,
            )
        appt = _reload_appt(appt.pk)
        kind = "rdv_annule" if appt.statut == Appointment.Statut.ANNULE else "rdv_updated"
        when = appt.debut.strftime("%d/%m/%Y %H:%M") if appt.debut else ""
        if kind == "rdv_annule":
            title, body = "Rendez-vous annulé", f"Votre RDV du {when} a été annulé."
        else:
            title, body = "Rendez-vous modifié", f"Votre RDV a été mis à jour ({when})."
        publish_appointment(appt, actor=user, kind=kind, title=title, body=body)
        return response

    def destroy(self, request, *args, **kwargs):
        if request.user.role == Roles.PATIENT:
            raise PermissionDenied("Annulez le RDV plutôt que de le supprimer.")
        if not role_can_write(request.user.role, "rdv"):
            raise PermissionDenied("Suppression RDV non autorisée.")
        appt = _reload_appt(self.get_object().pk)
        when = appt.debut.strftime("%d/%m/%Y %H:%M") if appt.debut else ""
        publish_appointment(
            appt,
            actor=request.user,
            kind="rdv_annule",
            title="Rendez-vous annulé",
            body=f"Votre RDV du {when} a été annulé.",
        )
        return super().destroy(request, *args, **kwargs)
