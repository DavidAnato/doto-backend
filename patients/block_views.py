"""API blacklist / blocage d'accès permanent."""
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.utils import log_action
from core.permissions import IsAdmin, Roles
from patients.access import revoke_access_grant
from patients.models import AccessBlock, AccessRequest, Patient

from .serializers import AccessBlockSerializer


def _patient_for_user(user):
    return getattr(user, "patient", None)


def _serialize(block: AccessBlock) -> dict:
    return AccessBlockSerializer(block).data


class AccessBlockListCreateView(APIView):
    """
    Patient : lister / créer un blocage (pro ou structure).
    Admin : lister tous / forcer un blocage.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        active_only = request.query_params.get("active") in ("1", "true", "yes")
        if user.role == Roles.PATIENT:
            patient = _patient_for_user(user)
            if patient is None:
                return Response([])
            qs = AccessBlock.objects.filter(patient=patient).select_related(
                "blocked_user", "blocked_structure", "patient"
            )
        elif user.role == Roles.ADMIN or user.is_superuser:
            qs = AccessBlock.objects.select_related(
                "blocked_user", "blocked_structure", "patient"
            ).all()
            pid = request.query_params.get("patient")
            if pid:
                qs = qs.filter(patient_id=pid)
        else:
            return Response({"detail": "Non autorisé."}, status=status.HTTP_403_FORBIDDEN)
        if active_only:
            qs = qs.filter(active=True)
        return Response([_serialize(b) for b in qs[:100]])

    def post(self, request):
        user = request.user
        data = request.data
        blocked_user_id = data.get("blocked_user_id") or data.get("blocked_user")
        blocked_structure_id = data.get("blocked_structure_id") or data.get("blocked_structure")
        reason = (data.get("reason") or "").strip()

        if not blocked_user_id and not blocked_structure_id:
            return Response(
                {"detail": "blocked_user_id ou blocked_structure_id requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admin_force = False
        if user.role == Roles.PATIENT:
            patient = _patient_for_user(user)
            if patient is None:
                return Response({"detail": "Aucun dossier."}, status=status.HTTP_404_NOT_FOUND)
        elif user.role == Roles.ADMIN or user.is_superuser:
            patient_id = data.get("patient_id") or data.get("patient")
            if not patient_id:
                return Response(
                    {"detail": "patient_id requis pour un blocage admin."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            patient = Patient.objects.filter(pk=patient_id).first()
            if patient is None:
                return Response({"detail": "Patient introuvable."}, status=status.HTTP_404_NOT_FOUND)
            admin_force = True
        else:
            return Response({"detail": "Non autorisé."}, status=status.HTTP_403_FORBIDDEN)

        # Réactiver un bloc existant s'il y en a un inactif
        existing = AccessBlock.objects.filter(
            patient=patient,
            blocked_user_id=blocked_user_id or None,
            blocked_structure_id=blocked_structure_id or None,
        ).first()
        if existing:
            existing.active = True
            existing.reason = reason or existing.reason
            existing.lifted_at = None
            if admin_force:
                existing.created_by_admin = user
            existing.save()
            block = existing
        else:
            block = AccessBlock.objects.create(
                patient=patient,
                blocked_user_id=blocked_user_id or None,
                blocked_structure_id=blocked_structure_id or None,
                reason=reason,
                created_by_admin=user if admin_force else None,
            )

        # Révoquer les grants actifs concernés
        grant_qs = AccessRequest.objects.filter(
            patient=patient,
            status__in=(
                AccessRequest.Status.APPROVED,
                AccessRequest.Status.EMERGENCY_BYPASS,
                AccessRequest.Status.PENDING,
            ),
        )
        if blocked_user_id:
            grant_qs = grant_qs.filter(requester_id=blocked_user_id)
        if blocked_structure_id:
            grant_qs = grant_qs.filter(structure_id=blocked_structure_id)
        patient_user = getattr(patient, "user", None) or user
        for req in grant_qs:
            if req.has_active_grant or req.status == AccessRequest.Status.PENDING:
                if req.status == AccessRequest.Status.PENDING:
                    req.status = AccessRequest.Status.DENIED
                    req.responded_at = timezone.now()
                    req.save(update_fields=["status", "responded_at"])
                else:
                    revoke_access_grant(req, patient_user=patient_user)

        log_action(
            request,
            "access_block_create",
            target=f"block:{block.id}",
            patient_npi=patient.npi,
        )
        return Response(_serialize(block), status=status.HTTP_201_CREATED)


class AccessBlockLiftView(APIView):
    """Patient ou admin : lever un blocage."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        block = AccessBlock.objects.filter(pk=pk).select_related("patient").first()
        if block is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        user = request.user
        if user.role == Roles.PATIENT:
            patient = _patient_for_user(user)
            if not patient or block.patient_id != patient.pk:
                return Response({"detail": "Non autorisé."}, status=status.HTTP_403_FORBIDDEN)
        elif not (user.role == Roles.ADMIN or user.is_superuser):
            return Response({"detail": "Non autorisé."}, status=status.HTTP_403_FORBIDDEN)

        block.active = False
        block.lifted_at = timezone.now()
        block.save(update_fields=["active", "lifted_at"])
        log_action(
            request,
            "access_block_lift",
            target=f"block:{block.id}",
            patient_npi=block.patient.npi,
        )
        return Response(_serialize(block))


class AdminForceRevokeView(APIView):
    """Admin : forcer la révocation d'un AccessRequest."""

    permission_classes = [IsAdmin]

    def post(self, request, pk):
        req = AccessRequest.objects.filter(pk=pk).select_related("patient", "requester").first()
        if req is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        patient_user = getattr(req.patient, "user", None) or request.user
        if req.has_active_grant or req.status == AccessRequest.Status.PENDING:
            if req.status == AccessRequest.Status.PENDING:
                req.status = AccessRequest.Status.DENIED
                req.responded_at = timezone.now()
                req.save(update_fields=["status", "responded_at"])
            else:
                revoke_access_grant(req, patient_user=patient_user)
        log_action(
            request,
            "admin_force_revoke",
            target=f"access:{req.id}",
            patient_npi=req.patient.npi,
        )
        from patients.access import serialize_access_request

        return Response(serialize_access_request(req))
