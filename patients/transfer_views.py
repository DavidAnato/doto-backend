"""API transferts d'hôpital mère."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsProfessional

from .models import HopitalMereTransfer, Patient
from .transfer import (
    cancel_transfer,
    confirm_transfer,
    duration_from_request,
    inbox_transfers,
    initiate_transfer,
    respond_transfer,
    serialize_transfer_for,
)


class HopitalMereTransferListCreateView(APIView):
    permission_classes = [IsProfessional]

    def get(self, request):
        qs = inbox_transfers(request.user)[:50]
        return Response([serialize_transfer_for(t, request.user) for t in qs])

    def post(self, request):
        patient_id = request.data.get("patient_id") or request.data.get("patient")
        to_id = request.data.get("to_structure_id") or request.data.get("to_structure")
        patient = Patient.objects.filter(pk=patient_id).select_related("hopital_mere").first()
        if patient is None:
            return Response({"detail": "Patient introuvable."}, status=status.HTTP_404_NOT_FOUND)
        transfer = initiate_transfer(
            actor=request.user,
            patient=patient,
            to_structure_id=to_id,
            duration_hours=duration_from_request(request.data),
        )
        return Response(serialize_transfer_for(transfer, request.user), status=status.HTTP_201_CREATED)


class _TransferActionView(APIView):
    permission_classes = [IsProfessional]

    def _get(self, pk):
        return (
            HopitalMereTransfer.objects.select_related(
                "patient", "from_structure", "to_structure", "requested_by"
            )
            .filter(pk=pk)
            .first()
        )


class HopitalMereTransferApproveView(_TransferActionView):
    def post(self, request, pk):
        transfer = self._get(pk)
        if transfer is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        transfer = respond_transfer(transfer, approve=True, actor=request.user)
        return Response(serialize_transfer_for(transfer, request.user))


class HopitalMereTransferDenyView(_TransferActionView):
    def post(self, request, pk):
        transfer = self._get(pk)
        if transfer is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        transfer = respond_transfer(transfer, approve=False, actor=request.user)
        return Response(serialize_transfer_for(transfer, request.user))


class HopitalMereTransferConfirmView(_TransferActionView):
    def post(self, request, pk):
        transfer = self._get(pk)
        if transfer is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        transfer = confirm_transfer(transfer, actor=request.user)
        return Response(serialize_transfer_for(transfer, request.user))


class HopitalMereTransferCancelView(_TransferActionView):
    def post(self, request, pk):
        transfer = self._get(pk)
        if transfer is None:
            return Response({"detail": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        transfer = cancel_transfer(transfer, actor=request.user)
        return Response(serialize_transfer_for(transfer, request.user))
