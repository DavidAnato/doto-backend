"""API demandes d'accès / consentement patient."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsProfessional, Roles
from patients.access import (
    cancel_access_request,
    create_access_request,
    expire_stale_requests,
    prior_medecin_respond,
    respond_access_request,
    revoke_access_grant,
    serialize_access_request,
    touch_access_session,
)
from patients.models import AccessRequest, Patient


class AccessRequestCreateView(APIView):
    """Pro : créer une demande d'accès (recherche / ouverture dossier)."""

    permission_classes = [IsProfessional]

    def post(self, request):
        patient_id = request.data.get("patient_id") or request.data.get("patient")
        if not patient_id:
            return Response({"detail": "patient_id requis."}, status=status.HTTP_400_BAD_REQUEST)
        patient = Patient.objects.filter(pk=patient_id).first()
        if patient is None:
            return Response({"detail": "Patient introuvable."}, status=status.HTTP_404_NOT_FOUND)

        emergency = bool(request.data.get("emergency") or request.data.get("urgence"))
        reason = (request.data.get("reason") or "").strip()
        mode = request.data.get("mode") or AccessRequest.Mode.SEARCH
        pin = (request.data.get("pin") or "").strip() or None
        from patients.hopital import parse_duration_hours

        duration_hours = parse_duration_hours(request.data)

        req = create_access_request(
            requester=request.user,
            patient=patient,
            mode=mode,
            reason=reason,
            emergency=emergency,
            request=request,
            duration_hours=duration_hours,
        )

        # Grant stale réutilisé : valider PIN pour rouvrir
        if getattr(req, "_pin_required", False) and pin:
            req = touch_access_session(req, requester=request.user, pin=pin)
            req._pin_required = False  # type: ignore[attr-defined]
        elif getattr(req, "_pin_required", False) and not pin:
            data = serialize_access_request(req)
            data["consent_required"] = False
            data["pin_required"] = True
            data["code"] = "pin_required"
            return Response(data, status=status.HTTP_200_OK)

        data = serialize_access_request(req)
        data["consent_required"] = req.status in (
            AccessRequest.Status.PENDING,
            AccessRequest.Status.PENDING_PRIOR,
        )
        data["emergency"] = req.status == AccessRequest.Status.EMERGENCY_BYPASS
        code = status.HTTP_201_CREATED
        if getattr(req, "_reused", False):
            code = status.HTTP_200_OK
        return Response(data, status=code)


class AccessRequestListView(APIView):
    """Patient : demandes en cours / historique. Pro : ses demandes (+ prior)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        expire_stale_requests()
        user = request.user
        status_filter = request.query_params.get("status")
        pending_only = request.query_params.get("pending") in ("1", "true", "yes")
        active_only = request.query_params.get("active") in ("1", "true", "yes")
        prior_only = request.query_params.get("prior") in ("1", "true", "yes")
        inbox = request.query_params.get("inbox") in ("1", "true", "yes")

        if inbox and user.role != Roles.PATIENT:
            from patients.hopital import access_inbox_qs

            qs = access_inbox_qs(user)
            return Response([serialize_access_request(r) for r in qs[:50]])

        if user.role == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            if patient is None:
                return Response([])
            qs = AccessRequest.objects.filter(patient=patient).select_related(
                "requester", "structure", "patient", "prior_medecin"
            )
        elif prior_only:
            qs = AccessRequest.objects.filter(
                prior_medecin=user,
                status=AccessRequest.Status.PENDING_PRIOR,
            ).select_related("requester", "structure", "patient", "prior_medecin")
        else:
            qs = AccessRequest.objects.filter(
                Q(requester=user) | Q(prior_medecin=user)
            ).select_related("requester", "structure", "patient", "prior_medecin")

        if pending_only:
            if user.role == Roles.PATIENT:
                qs = qs.filter(status=AccessRequest.Status.PENDING)
            else:
                qs = qs.filter(
                    status__in=(
                        AccessRequest.Status.PENDING,
                        AccessRequest.Status.PENDING_PRIOR,
                    )
                )
            return Response([serialize_access_request(r) for r in qs[:50]])
        if active_only:
            rows = [
                r
                for r in qs.order_by("-created_at")[:80]
                if r.has_active_grant
                and r.status != AccessRequest.Status.EMERGENCY_BYPASS
                and (r.scope or "") != "urgence"
            ]
            return Response([serialize_access_request(r) for r in rows[:50]])
        if status_filter:
            qs = qs.filter(status=status_filter)

        return Response([serialize_access_request(r) for r in qs[:50]])


class AccessRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        expire_stale_requests()
        req = (
            AccessRequest.objects.select_related(
                "requester", "patient", "structure", "prior_medecin"
            )
            .filter(pk=pk)
            .first()
        )
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        user = request.user
        ok = False
        if user.role == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            ok = bool(patient and patient.pk == req.patient_id)
        elif user.role in Roles.PROFESSIONALS:
            from patients.hopital import can_decide_hopital_mere

            ok = (
                req.requester_id == user.id
                or req.prior_medecin_id == user.id
                or user.role == Roles.ADMIN
                or can_decide_hopital_mere(user, req.patient, req)
            )
        if not ok:
            return Response({"detail": "Non autorisé."}, status=status.HTTP_403_FORBIDDEN)
        return Response(serialize_access_request(req))


class AccessRequestApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        return self._respond(request, pk, True)

    def _respond(self, request, pk, approve: bool):
        req = (
            AccessRequest.objects.select_related("patient", "requester", "decision_structure")
            .filter(pk=pk)
            .first()
        )
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        from patients.hopital import hopital_mere_respond, patient_controls_external_requests

        if (
            req.route == AccessRequest.Route.HOPITAL_MERE
            and not patient_controls_external_requests(req.patient)
        ):
            if request.user.role == Roles.PATIENT:
                return Response(
                    {"detail": "Cette demande est traitée par l'hôpital mère."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            req = hopital_mere_respond(req, approve=approve, actor=request.user)
            return Response(serialize_access_request(req))

        if request.user.role != Roles.PATIENT:
            return Response({"detail": "Réservé au patient."}, status=status.HTTP_403_FORBIDDEN)
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response({"detail": "Aucun dossier patient."}, status=status.HTTP_404_NOT_FOUND)
        req = AccessRequest.objects.filter(pk=pk, patient=patient).first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        req = respond_access_request(req, approve=approve, patient_user=request.user)
        return Response(serialize_access_request(req))


class AccessRequestDenyView(AccessRequestApproveView):
    def post(self, request, pk):
        return self._respond(request, pk, False)


class AccessRequestPriorApproveView(APIView):
    """Médecin précédent : valide le transfert d'accès."""

    permission_classes = [IsProfessional]

    def post(self, request, pk):
        return self._respond(request, pk, True)

    def _respond(self, request, pk, approve: bool):
        req = AccessRequest.objects.filter(pk=pk).first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        req = prior_medecin_respond(req, approve=approve, prior_user=request.user)
        return Response(serialize_access_request(req))


class AccessRequestPriorDenyView(AccessRequestPriorApproveView):
    def post(self, request, pk):
        return self._respond(request, pk, False)


class AccessRequestTouchView(APIView):
    """Pro : rouvre un grant (PIN si stale)."""

    permission_classes = [IsProfessional]

    def post(self, request, pk):
        req = AccessRequest.objects.filter(pk=pk).first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        pin = (request.data.get("pin") or "").strip() or None
        req = touch_access_session(req, requester=request.user, pin=pin)
        return Response(serialize_access_request(req))


class AccessRequestCancelView(APIView):
    """Pro : annule sa demande encore en attente (le patient n'a plus à répondre)."""

    permission_classes = [IsProfessional]

    def post(self, request, pk):
        req = AccessRequest.objects.filter(pk=pk).first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        try:
            req = cancel_access_request(req, requester=request.user)
        except Exception as e:
            from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

            if isinstance(e, (PermissionDenied, ValidationError, APIException)):
                raise
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_access_request(req))


class AccessRequestRevokeView(APIView):
    """Patient : révoque un grant encore actif."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if request.user.role != Roles.PATIENT:
            return Response({"detail": "Réservé au patient."}, status=status.HTTP_403_FORBIDDEN)
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response({"detail": "Aucun dossier patient."}, status=status.HTTP_404_NOT_FOUND)
        req = AccessRequest.objects.filter(pk=pk, patient=patient).first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if not req.has_active_grant:
            return Response(
                {"detail": "Aucun accès actif à révoquer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req = revoke_access_grant(req, patient_user=request.user)
        return Response(serialize_access_request(req))
