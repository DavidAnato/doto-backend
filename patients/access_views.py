"""API demandes d'accès / consentement patient."""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsProfessional, Roles
from patients.access import (
    cancel_access_request,
    create_access_request,
    expire_stale_requests,
    respond_access_request,
    revoke_access_grant,
    serialize_access_request,
)
from patients.models import AccessRequest, Patient
from django.utils import timezone


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

        req = create_access_request(
            requester=request.user,
            patient=patient,
            mode=mode,
            reason=reason,
            emergency=emergency,
            request=request,
        )
        data = serialize_access_request(req)
        data["consent_required"] = req.status == AccessRequest.Status.PENDING
        data["emergency"] = req.status == AccessRequest.Status.EMERGENCY_BYPASS
        code = status.HTTP_201_CREATED
        if getattr(req, "_reused", False):
            code = status.HTTP_200_OK
        return Response(data, status=code)


class AccessRequestListView(APIView):
    """Patient : demandes en cours / historique. Pro : ses demandes."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        expire_stale_requests()
        user = request.user
        status_filter = request.query_params.get("status")
        pending_only = request.query_params.get("pending") in ("1", "true", "yes")
        active_only = request.query_params.get("active") in ("1", "true", "yes")

        if user.role == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            if patient is None:
                return Response([])
            qs = AccessRequest.objects.filter(patient=patient).select_related(
                "requester", "structure", "patient"
            )
        else:
            qs = AccessRequest.objects.filter(requester=user).select_related(
                "requester", "structure", "patient"
            )

        if pending_only:
            qs = qs.filter(status=AccessRequest.Status.PENDING)
            return Response([serialize_access_request(r) for r in qs[:50]])
        if active_only:
            now = timezone.now()
            qs = qs.filter(
                status__in=(
                    AccessRequest.Status.APPROVED,
                    AccessRequest.Status.EMERGENCY_BYPASS,
                ),
                grant_expires_at__gt=now,
            )
            return Response([serialize_access_request(r) for r in qs[:50]])
        if status_filter:
            qs = qs.filter(status=status_filter)

        return Response([serialize_access_request(r) for r in qs[:50]])


class AccessRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        expire_stale_requests()
        req = AccessRequest.objects.select_related("requester", "patient", "structure").filter(pk=pk).first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        user = request.user
        ok = False
        if user.role == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            ok = bool(patient and patient.pk == req.patient_id)
        elif user.role in Roles.PROFESSIONALS:
            ok = req.requester_id == user.id or user.role == Roles.ADMIN
        if not ok:
            return Response({"detail": "Non autorisé."}, status=status.HTTP_403_FORBIDDEN)
        return Response(serialize_access_request(req))


class AccessRequestApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        return self._respond(request, pk, True)

    def _respond(self, request, pk, approve: bool):
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
