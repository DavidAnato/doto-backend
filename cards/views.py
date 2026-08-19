import io
import json
import queue
import time

import qrcode
from django.db.models import Count, Q
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from audit.utils import log_action
from core.permissions import IsAdmin, IsProfessional, Roles
from patients.models import Patient
from patients.serializers import UrgenceSerializer

from . import services
from .models import DodoCard
from .pdf import build_dodocard_pdf
from .pubsub import hub_bus
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
        log_action(request, "scan_dodocard", target=f"card:{card.id}", patient_npi=card.patient.npi)

        req = create_access_request(
            requester=request.user,
            patient=card.patient,
            mode=AccessRequest.Mode.SCAN,
            reason="Scan DotoCard",
            dodocard=card,
            emergency=emergency,
            request=request,
        )

        consent_required = req.status == AccessRequest.Status.PENDING
        is_emergency = req.status == AccessRequest.Status.EMERGENCY_BYPASS

        # Toujours notifier le Hub du MÊME pro (user_id) - y compris consent pending
        # et réutilisation grant/pending (create_access_request ne republie pas alors).
        # Canal ciblé : pas de broadcast global.
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
            "ts": timezone.now().isoformat(),
        }
        hub_notified = hub_bus.publish(request.user.id, event) > 0

        return Response(
            {
                "patient_id": card.patient.id,
                "npi": card.patient.npi,
                "urgence": UrgenceSerializer(card.patient).data,
                "hub_notified": hub_notified,
                "consent_required": consent_required,
                "emergency": is_emergency,
                "access_request": serialize_access_request(req),
                "message": (
                    "En attente de confirmation patient…"
                    if consent_required
                    else (
                        "Accès urgence - consentement non requis."
                        if is_emergency
                        else "Accès autorisé."
                    )
                ),
            }
        )


def _authenticate_access_token(raw: str):
    """Valide un JWT access (header Bearer ou query ?access=)."""
    if not raw:
        return None
    try:
        token = AccessToken(raw)
        return JWTAuthentication().get_user(token)
    except (InvalidToken, TokenError, Exception):
        return None


class HubEventsView(APIView):
    """Flux SSE authentifié pour DotoHub.

    EventSource ne peut pas envoyer Authorization → passer `?access=<jwt>`.
    Événements : `connected`, `dodocard_scan`, `access_*`, keepalive commentaires.
    Canal = user_id du JWT (pas de broadcast inter-pros).
    """

    authentication_classes = []
    permission_classes = [AllowAny]

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
            q = hub_bus.subscribe(user_id)
            try:
                hello = {"type": "connected", "user_id": user_id, "ts": timezone.now().isoformat()}
                yield f"data: {json.dumps(hello)}\n\n"
                while True:
                    try:
                        event = q.get(timeout=15)
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                    except queue.Empty:
                        # Commentaire + event ping (certains proxies coupent les seuls commentaires)
                        yield f": keepalive {int(time.time())}\n\n"
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "type": "ping",
                                    "ts": timezone.now().isoformat(),
                                }
                            )
                            + "\n\n"
                        )
            finally:
                hub_bus.unsubscribe(user_id, q)

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        # EventSource cross-origin : corsheaders gère Access-Control-Allow-Origin
        return response


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
            if p:
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
                },
                "patients_recents": recent_patients,
                "genere_le": now,
            }
        )
