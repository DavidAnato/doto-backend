import io

import qrcode
from django.db.models import Count, Q
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from audit.utils import log_action
from core.permissions import IsAdmin, IsProfessional, Roles
from core.renderers import EventStreamRenderer
from patients.models import Patient
from patients.serializers import UrgenceSerializer

from . import services
from .models import DodoCard
from .pdf import build_dodocard_pdf
from .pubsub import hub_bus, remember_hub_event, sse_stream, take_hub_events
from .serializers import DodoCardSerializer, ScanSerializer


def _patient_for(user):
    return getattr(user, "patient", None)


def _active_card(patient):
    return (
        DodoCard.objects.filter(patient=patient, statut=DodoCard.Statut.ACTIVE)
        .order_by("-date_creation")
        .first()
    )


def _first_card_missing(patient):
    """Champs minimum pour une première émission (sang / allergies = optionnels)."""
    missing = []
    if not (patient.photo or (getattr(patient, "user", None) and patient.user.photo)):
        missing.append("photo")
    if not (patient.nom or "").strip() or not (patient.prenom or "").strip():
        missing.append("identité")
    if not patient.date_naissance:
        missing.append("date de naissance")
    return missing


def _ensure_ready_for_issue(patient):
    missing = _first_card_missing(patient)
    if not missing:
        return None
    return Response(
        {
            "detail": (
                "Pour générer votre première DotoCard, complétez : "
                + ", ".join(missing)
                + ". Les autres champs (groupe sanguin, etc.) sont optionnels."
            ),
            "missing": missing,
            "code": "profile_incomplete_for_card",
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _notify_card_event(patient, *, title: str, body: str, card_id: int, event: str):
    try:
        from notifications.models import Notification
        from notifications.services import notify_admins, notify_user

        user = getattr(patient, "user", None)
        if user:
            notify_user(
                user,
                title=title,
                body=body,
                type=Notification.Type.SYSTEM,
                payload={"card_id": card_id, "event": event},
            )
        notify_admins(
            title=title,
            body=body,
            type=Notification.Type.SYSTEM,
            payload={"card_id": card_id, "patient_npi": patient.npi, "event": event},
        )
    except Exception:
        pass


class DodoCardViewSet(viewsets.ModelViewSet):
    queryset = DodoCard.objects.select_related("patient").all()
    serializer_class = DodoCardSerializer
    filterset_fields = ["statut", "patient"]
    search_fields = ["patient__npi", "patient__nom"]

    def get_permissions(self):
        patient_actions = (
            "list",
            "retrieve",
            "qr",
            "pdf",
            "mine",
            "mine_pdf",
            "report_loss",
            "request_reissue",
        )
        if self.action in patient_actions:
            return [IsAuthenticated()]
        if self.action == "scan":
            return [IsProfessional()]
        return [IsAdmin()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "role", None) == "patient":
            patient = _patient_for(user)
            if patient:
                return qs.filter(patient=patient)
            return qs.none()
        return qs

    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        """DotoCard active du patient connecté (app DotoPlus)."""
        patient = _patient_for(request.user)
        if patient is None:
            return Response({"detail": "Aucun dossier patient."}, status=status.HTTP_404_NOT_FOUND)
        card = _active_card(patient)
        if card is None:
            return Response({"detail": "Aucune DotoCard active."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DodoCardSerializer(card).data)

    @action(detail=False, methods=["get"], url_path="mine/pdf")
    def mine_pdf(self, request):
        """PDF imprimable de la DotoCard active."""
        patient = _patient_for(request.user)
        if patient is None:
            return Response({"detail": "Aucun dossier patient."}, status=status.HTTP_404_NOT_FOUND)
        card = _active_card(patient)
        if card is None:
            return Response({"detail": "Aucune DotoCard active."}, status=status.HTTP_404_NOT_FOUND)
        pdf_bytes = build_dodocard_pdf(card)
        log_action(
            request,
            "telecharger_dodocard_pdf",
            target=f"card:{card.id}",
            patient_npi=patient.npi,
        )
        filename = f"DotoCard_{patient.npi}_{card.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["post"], url_path="mine/report-loss")
    def report_loss(self, request):
        """Patient : signaler perte → révocation + réémission auto (< 1 min)."""
        patient = _patient_for(request.user)
        if patient is None:
            return Response({"detail": "Aucun dossier patient."}, status=status.HTTP_404_NOT_FOUND)
        motif = (request.data.get("motif") or request.data.get("reason") or "perte").strip()
        old = _active_card(patient)
        if old is None:
            # Pas de carte active : émettre directement
            blocked = _ensure_ready_for_issue(patient)
            if blocked is not None:
                return blocked
            card = DodoCard.issue(patient)
            log_action(
                request,
                "emettre_dodocard",
                target=f"card:{card.id}",
                patient_npi=patient.npi,
            )
            return Response(
                {
                    "detail": "Aucune carte active - nouvelle carte émise.",
                    "old_card": None,
                    "card": DodoCardSerializer(card).data,
                },
                status=status.HTTP_201_CREATED,
            )

        started = timezone.now()
        card = DodoCard.replace(old, user=request.user, motif=motif or "perte", mark_lost=True)
        elapsed_ms = int((timezone.now() - started).total_seconds() * 1000)
        log_action(
            request,
            "signaler_perte_dodocard",
            target=f"card:{old.id}->card:{card.id}",
            patient_npi=patient.npi,
        )
        log_action(
            request,
            "reemettre_dodocard",
            target=f"card:{card.id}",
            patient_npi=patient.npi,
        )
        _notify_card_event(
            patient,
            title="DotoCard - perte signalée",
            body="Votre ancienne carte est invalidée. Une nouvelle DotoCard a été émise.",
            card_id=card.id,
            event="report_loss",
        )
        return Response(
            {
                "detail": "Perte signalée. Ancienne carte révoquée, nouvelle carte émise.",
                "reissue_ms": elapsed_ms,
                "old_card": DodoCardSerializer(old).data,
                "card": DodoCardSerializer(card).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="mine/reissue")
    def request_reissue(self, request):
        """Patient : demander une nouvelle carte (remplace l'active)."""
        patient = _patient_for(request.user)
        if patient is None:
            return Response({"detail": "Aucun dossier patient."}, status=status.HTTP_404_NOT_FOUND)
        motif = (request.data.get("motif") or request.data.get("reason") or "demande_remplacement").strip()
        old = _active_card(patient)
        if old is None:
            blocked = _ensure_ready_for_issue(patient)
            if blocked is not None:
                return blocked
            card = DodoCard.issue(patient)
            log_action(
                request,
                "emettre_dodocard",
                target=f"card:{card.id}",
                patient_npi=patient.npi,
            )
            _notify_card_event(
                patient,
                title="DotoCard émise",
                body="Votre nouvelle DotoCard est disponible.",
                card_id=card.id,
                event="issue",
            )
            return Response(DodoCardSerializer(card).data, status=status.HTTP_201_CREATED)

        started = timezone.now()
        card = DodoCard.replace(old, user=request.user, motif=motif, mark_lost=False)
        elapsed_ms = int((timezone.now() - started).total_seconds() * 1000)
        log_action(
            request,
            "reemettre_dodocard",
            target=f"card:{old.id}->card:{card.id}",
            patient_npi=patient.npi,
        )
        _notify_card_event(
            patient,
            title="DotoCard renouvelée",
            body="Votre ancienne carte est invalidée. Présentez le nouveau QR.",
            card_id=card.id,
            event="reissue",
        )
        return Response(
            {
                "detail": "Carte réémise.",
                "reissue_ms": elapsed_ms,
                "old_card": DodoCardSerializer(old).data,
                "card": DodoCardSerializer(card).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def create(self, request, *args, **kwargs):
        """Émission d'une DotoCard pour un patient (CDC §2.4)."""
        patient = Patient.objects.filter(pk=request.data.get("patient")).first()
        if patient is None:
            return Response({"detail": "Patient introuvable."}, status=status.HTTP_400_BAD_REQUEST)
        # Une seule carte active à la fois
        existing = _active_card(patient)
        if existing is not None:
            existing.mark_reissued(user=request.user, motif="remplacee_admin")
        card = DodoCard.issue(patient, cvv=request.data.get("cvv", ""))
        log_action(request, "emettre_dodocard", target=f"card:{card.id}", patient_npi=patient.npi)
        _notify_card_event(
            patient,
            title="DotoCard émise",
            body="Une DotoCard a été émise pour votre dossier.",
            card_id=card.id,
            event="issue",
        )
        return Response(DodoCardSerializer(card).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Signalement perte → invalidation token < 1 min (CDC §2.4, §7)."""
        card = self.get_object()
        motif = (request.data.get("motif") or request.data.get("reason") or "perte").strip()
        card.revoke(user=request.user, motif=motif, mark_lost=True)
        log_action(
            request,
            "revoquer_dodocard",
            target=f"card:{card.id}",
            patient_npi=card.patient.npi,
        )
        _notify_card_event(
            card.patient,
            title="DotoCard révoquée",
            body="Votre DotoCard a été invalidée. Demandez une nouvelle carte si besoin.",
            card_id=card.id,
            event="revoke",
        )
        return Response(DodoCardSerializer(card).data)

    @action(detail=True, methods=["post"])
    def reissue(self, request, pk=None):
        """Réémission : ancien token révoqué, nouveau généré, dossier intact."""
        old = self.get_object()
        motif = (request.data.get("motif") or request.data.get("reason") or "reemission_admin").strip()
        card = DodoCard.replace(old, user=request.user, motif=motif, mark_lost=False)
        log_action(
            request,
            "reemettre_dodocard",
            target=f"card:{old.id}->card:{card.id}",
            patient_npi=card.patient.npi,
        )
        _notify_card_event(
            card.patient,
            title="DotoCard réémise",
            body="Une nouvelle DotoCard remplace votre ancienne carte.",
            card_id=card.id,
            event="reissue",
        )
        return Response(DodoCardSerializer(card).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def qr(self, request, pk=None):
        """Image PNG du QR code haute résolution (CDC §4.8)."""
        card = self.get_object()
        img = qrcode.make(card.token_chiffre, error_correction=qrcode.constants.ERROR_CORRECT_M)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return HttpResponse(buffer.getvalue(), content_type="image/png")

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """PDF d'une carte (admin / patient propriétaire)."""
        card = self.get_object()
        pdf_bytes = build_dodocard_pdf(card)
        filename = f"DotoCard_{card.patient.npi}_{card.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ScanView(APIView):
    """Résout un token scanné → AccessRequest + urgence minimale.

    Sans urgence : crée une demande pending, notifie le patient (SSE/push),
    et attend confirmation avant d'ouvrir le dossier complet (SSE access_granted).
    Avec emergency=true ou rôle ambulancier : bypass consentement (audité).
    """

    permission_classes = [IsProfessional]

    def post(self, request):
        from patients.access import create_access_request, serialize_access_request
        from patients.models import AccessRequest

        serializer = ScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]
        emergency = bool(
            request.data.get("emergency")
            or request.data.get("urgence")
            or serializer.validated_data.get("emergency")
        )

        card = DodoCard.objects.select_related("patient", "patient__user").filter(
            token_chiffre=token
        ).first()
        if card is None or not services.is_valid_token(token):
            return Response({"detail": "Token invalide ou inconnu."}, status=status.HTTP_404_NOT_FOUND)
        if not card.is_active:
            return Response(
                {"detail": "Carte révoquée ou expirée.", "statut": card.statut},
                status=status.HTTP_410_GONE,
            )
        log_action(
            request,
            "scan_dodocard",
            target=f"card:{card.id}|structure:{getattr(request.user, 'structure_principale_id', None) or '-'}",
            patient_npi=card.patient.npi,
        )

        from patients.hopital import needs_duration_gate, parse_duration_hours

        duration_hours = parse_duration_hours(request.data)
        if needs_duration_gate(request.user, card.patient, emergency=emergency) and not duration_hours:
            event = {
                "type": "dodocard_scan",
                "patient_id": card.patient.id,
                "npi": card.patient.npi,
                "full_name": card.patient.full_name,
                "token": token[:12] + "…",
                "scanned_by": request.user.id,
                "emergency": False,
                "consent_required": True,
                "duration_required": True,
                "dossier_visible": False,
                "message": "Indiquez la durée d'accès. Le dossier reste fermé.",
                "ts": timezone.now().isoformat(),
            }
            remember_hub_event(request.user.id, event)
            hub_bus.publish(request.user.id, event)
            return Response(
                {
                    "patient_id": card.patient.id,
                    "npi": card.patient.npi,
                    "full_name": card.patient.full_name,
                    "consent_required": True,
                    "duration_required": True,
                    "dossier_visible": False,
                    "emergency": False,
                    "message": "Indiquez la durée d'accès. Le dossier reste fermé.",
                }
            )

        req = create_access_request(
            requester=request.user,
            patient=card.patient,
            mode=AccessRequest.Mode.SCAN,
            reason="Scan DotoCard",
            dodocard=card,
            emergency=emergency,
            request=request,
            duration_hours=duration_hours,
        )

        waiting_patient = req.status == AccessRequest.Status.PENDING
        waiting_prior = req.status == AccessRequest.Status.PENDING_PRIOR
        consent_required = waiting_patient or waiting_prior
        is_emergency = req.status == AccessRequest.Status.EMERGENCY_BYPASS
        from patients.access import grant_allows_full

        full_open = grant_allows_full(req)
        access_expired = (not full_open) and (not is_emergency) and (not consent_required)
        hide_dossier = (not full_open) and (not is_emergency)
        if consent_required and getattr(req, "route", "") == AccessRequest.Route.HOPITAL_MERE:
            hide_dossier = True
        if is_emergency:
            message = "Accès urgence - données critiques uniquement."
        elif hide_dossier and consent_required and getattr(req, "route", "") == AccessRequest.Route.HOPITAL_MERE:
            message = "Demande envoyée à l'hôpital mère. Le dossier reste fermé tant qu'elle n'a pas accepté."
        elif waiting_patient:
            message = "En attente de confirmation patient…"
        elif waiting_prior:
            message = "Le patient a accepté. En attente de l'ancien médecin…"
        elif getattr(req, "direct_structure", False) and full_open:
            message = "Accès immédiat — hôpital mère."
        elif full_open:
            message = "Accès autorisé."
        elif access_expired:
            message = "Cet accès a expiré"
        else:
            message = "Le dossier reste fermé."

        event = {
            "type": "dodocard_scan",
            "patient_id": card.patient.id,
            "npi": card.patient.npi,
            "full_name": card.patient.full_name,
            "token": token[:12] + "…",
            "scanned_by": request.user.id,
            "access_request_id": req.id,
            "emergency": is_emergency,
            "consent_required": consent_required,
            "dossier_visible": not hide_dossier,
            "access_expired": access_expired,
            "route": req.route,
            "message": message,
            "ts": timezone.now().isoformat(),
        }
        remember_hub_event(request.user.id, event)
        hub_notified = hub_bus.publish(request.user.id, event) > 0

        payload = {
            "patient_id": card.patient.id,
            "npi": card.patient.npi,
            "full_name": card.patient.full_name,
            "hub_notified": hub_notified,
            "consent_required": consent_required,
            "emergency": is_emergency,
            "dossier_visible": not hide_dossier,
            "access_expired": access_expired,
            "duration_required": False,
            "access_request": serialize_access_request(req),
            "message": message,
        }
        if is_emergency or not hide_dossier:
            payload["urgence"] = UrgenceSerializer(card.patient).data
        return Response(payload)


def _authenticate_access_token(raw: str):
    """Valide un JWT access (header Bearer ou query ?access=)."""
    if not raw:
        return None
    try:
        token = AccessToken(raw)
        return JWTAuthentication().get_user(token)
    except (InvalidToken, TokenError, Exception):
        return None


def _revalidate_hub_event(user, event: dict) -> dict:
    """Un événement en cache ne doit pas rouvrir un dossier dont la date de fin est passée."""
    if not isinstance(event, dict):
        return event
    if event.get("type") not in ("dodocard_scan", "access_granted"):
        return event
    if event.get("consent_required") or event.get("duration_required"):
        return event
    if event.get("access_expired"):
        return {
            "type": "access_expired",
            "patient_id": event.get("patient_id"),
            "npi": event.get("npi"),
            "full_name": event.get("full_name"),
            "message": "Cet accès a expiré",
            "ts": timezone.now().isoformat(),
        }
    patient_id = event.get("patient_id")
    if not patient_id:
        return event
    patient = Patient.objects.filter(pk=patient_id).first()
    if patient is None:
        return event
    from patients.access import can_read_dossier, has_active_grant
    from patients.models import AccessRequest

    if can_read_dossier(user, patient):
        return event
    if event.get("emergency"):
        grant = has_active_grant(user, patient)
        if (
            grant
            and grant.status == AccessRequest.Status.EMERGENCY_BYPASS
            and grant.has_active_grant
        ):
            opened = dict(event)
            opened["dossier_visible"] = False
            opened["message"] = "Accès urgence - données critiques uniquement."
            return opened
    return {
        "type": "access_expired",
        "patient_id": patient_id,
        "npi": event.get("npi"),
        "full_name": event.get("full_name"),
        "message": "Cet accès a expiré",
        "ts": timezone.now().isoformat(),
    }


class HubEventsView(APIView):
    """Flux SSE authentifié pour DotoHub.

    EventSource ne peut pas envoyer Authorization → passer `?access=<jwt>`.
    Événements : `connected`, `dodocard_scan`, `access_*`, keepalive commentaires.
    Canal = user_id du JWT (pas de broadcast inter-pros).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    # JSON + event-stream : sinon DRF répond 406 et EventSource boucle.
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        raw = request.GET.get("access") or ""
        if not raw:
            auth = request.META.get("HTTP_AUTHORIZATION", "")
            if auth.lower().startswith("bearer "):
                raw = auth.split(" ", 1)[1].strip()
        user = _authenticate_access_token(raw)
        if user is None or not user.is_authenticated:
            return Response({"detail": "Authentification requise."}, status=status.HTTP_401_UNAUTHORIZED)
        if getattr(user, "role", None) not in Roles.PROFESSIONALS:
            return Response({"detail": "Réservé aux professionnels."}, status=status.HTTP_403_FORBIDDEN)

        user_id = user.id

        def stream():
            hello = {"type": "connected", "user_id": user_id, "ts": timezone.now().isoformat()}
            yield from sse_stream(user_id, hello)

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        # EventSource cross-origin : corsheaders gère Access-Control-Allow-Origin
        return response


class HubLiveView(APIView):
    """Scans récents du pro connecté. Relève si l'EventSource du site s'est fermé."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if getattr(request.user, "role", None) not in Roles.PROFESSIONALS:
            return Response({"events": []})
        return Response({"events": [_revalidate_hub_event(request.user, ev) for ev in take_hub_events(request.user.id)]})


class DashboardView(APIView):
    """Statistiques back-office enrichies (CDC §3.5 Admin)."""

    permission_classes = [IsAdmin]

    def get(self, request):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.db.models.functions import TruncDate

        from accounts.models import StructureSante
        from audit.models import AuditLog
        from medical.models import Consultation, Examen, Ordonnance

        User = get_user_model()
        now = timezone.now()
        since_7d = now - timedelta(days=7)
        since_30d = now - timedelta(days=30)

        card_stats = {
            row["statut"]: row["total"]
            for row in DodoCard.objects.values("statut").annotate(total=Count("id"))
        }
        roles = list(
            User.objects.values("role").annotate(total=Count("id")).order_by("-total")
        )

        # Tendance consultations (30 derniers jours)
        trend_raw = (
            Consultation.objects.filter(date__gte=since_30d)
            .annotate(jour=TruncDate("date"))
            .values("jour")
            .annotate(total=Count("id"))
            .order_by("jour")
        )
        trend_map = {row["jour"].isoformat(): row["total"] for row in trend_raw if row["jour"]}
        consultations_trend = []
        for i in range(29, -1, -1):
            day = (now - timedelta(days=i)).date()
            consultations_trend.append(
                {"date": day.isoformat(), "total": trend_map.get(day.isoformat(), 0)}
            )

        recent_audit = list(
            AuditLog.objects.select_related("user").order_by("-timestamp")[:12].values(
                "id",
                "username",
                "action",
                "target",
                "patient_npi",
                "ip",
                "timestamp",
            )
        )

        structures = list(
            StructureSante.objects.filter(statut_partenaire=True)
            .order_by("nom")[:8]
            .values("id", "nom", "type", "localisation", "code_structure")
        )

        # Échecs login / OTP d'après le journal d'audit (actions connues)
        auth_failures = AuditLog.objects.filter(timestamp__gte=since_7d).filter(
            Q(action__icontains="login_fail")
            | Q(action__icontains="otp_fail")
            | Q(action__icontains="echec_connexion")
            | Q(action__icontains="échec")
            | Q(action__icontains="echec_auth")
        ).count()
        locked_users = User.objects.filter(locked_until__gt=now).count()
        pros = User.objects.filter(
            role__in=[
                "medecin",
                "infirmier",
                "pharmacien",
                "laborantin",
                "ambulancier",
                "receptionniste",
                "admin",
            ]
        ).count()

        return Response(
            {
                "utilisateurs": User.objects.count(),
                "professionnels": pros,
                "patients": Patient.objects.count(),
                "structures": StructureSante.objects.count(),
                "consultations": Consultation.objects.count(),
                "consultations_7j": Consultation.objects.filter(date__gte=since_7d).count(),
                "consultations_30j": Consultation.objects.filter(date__gte=since_30d).count(),
                "ordonnances_actives": Ordonnance.objects.filter(statut="active").count(),
                "examens": Examen.objects.count(),
                "dodocards_actives": card_stats.get("active", 0),
                "dodocards_revoquees": card_stats.get("revoquee", 0),
                "dodocards_expirees": card_stats.get("expiree", 0),
                "dodocards_reemises": card_stats.get("reemise", 0),
                "dodocards_total": DodoCard.objects.count(),
                "dodocards_statut": [
                    {"statut": "active", "label": "Actives", "total": card_stats.get("active", 0)},
                    {"statut": "revoquee", "label": "Révoquées", "total": card_stats.get("revoquee", 0)},
                    {"statut": "expiree", "label": "Expirées", "total": card_stats.get("expiree", 0)},
                    {"statut": "reemise", "label": "Réémises", "total": card_stats.get("reemise", 0)},
                ],
                "evenements_audit": AuditLog.objects.count(),
                "evenements_audit_7j": AuditLog.objects.filter(timestamp__gte=since_7d).count(),
                "echec_auth_7j": auth_failures,
                "comptes_verrouilles": locked_users,
                "repartition_roles": roles,
                "consultations_trend": consultations_trend,
                "audit_recent": recent_audit,
                "structures_recentes": structures,
                "genere_le": now,
            }
        )


class HubDashboardView(APIView):
    """Vue d'accueil professionnels (DotoHub) - stats légères et raccourcis."""

    permission_classes = [IsProfessional]

    def get(self, request):
        from datetime import timedelta

        from audit.models import AuditLog
        from medical.models import Consultation, Ordonnance

        user = request.user
        now = timezone.now()
        since_7d = now - timedelta(days=7)

        # Patients récemment consultés / scannés via audit du pro
        recent_npi = (
            AuditLog.objects.filter(user=user)
            .exclude(patient_npi="")
            .order_by("-timestamp")
            .values_list("patient_npi", flat=True)[:20]
        )
        seen = set()
        recent_patients = []
        from accounts.photo_utils import patient_photo_url, user_photo_url

        for npi in recent_npi:
            if npi in seen:
                continue
            seen.add(npi)
            p = Patient.objects.filter(npi=npi).select_related("user").first()
            if p is None:
                continue
            from patients.access import can_read_dossier

            if not can_read_dossier(user, p):
                continue
            recent_patients.append(
                    {
                        "id": p.id,
                        "npi": p.npi,
                        "nom": p.nom,
                        "prenom": p.prenom,
                        "full_name": p.full_name,
                        "photo_url": patient_photo_url(p, request=request),
                    }
                )
            if len(recent_patients) >= 5:
                break

        structures = []
        if hasattr(user, "structures"):
            structures = list(
                user.structures.all().values("id", "nom", "type", "localisation")[:5]
            )
        principale = None
        if getattr(user, "structure_principale", None):
            sp = user.structure_principale
            principale = {
                "id": sp.id,
                "nom": sp.nom,
                "type": sp.type,
                "localisation": sp.localisation,
            }

        my_consultations_7j = Consultation.objects.filter(
            medecin=user, date__gte=since_7d
        ).count()
        ordonnances_actives = Ordonnance.objects.filter(
            medecin=user, statut="active"
        ).count()
        scans_7j = AuditLog.objects.filter(
            user=user, action__icontains="scan", timestamp__gte=since_7d
        ).count()

        from patients.access import serialize_access_request
        from patients.models import AccessRequest

        transferts = []
        if user.role == "medecin":
            for req in (
                AccessRequest.objects.filter(
                    prior_medecin=user,
                    status=AccessRequest.Status.PENDING_PRIOR,
                )
                .select_related("requester", "patient", "structure", "prior_medecin")
                .order_by("-created_at")[:10]
            ):
                transferts.append(serialize_access_request(req))

        return Response(
            {
                "role": user.role,
                "role_label": user.get_role_display(),
                "full_name": user.get_full_name() or user.username,
                "photo_url": user_photo_url(user, request=request),
                "structure_principale": principale,
                "structures": structures,
                "stats": {
                    "consultations_7j": my_consultations_7j,
                    "ordonnances_actives": ordonnances_actives,
                    "scans_7j": scans_7j,
                    "patients_recents": len(recent_patients),
                    "transferts_pending": len(transferts),
                },
                "patients_recents": recent_patients,
                "transferts_pending": transferts,
                "genere_le": now,
            }
        )
