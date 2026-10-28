from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.utils import log_action
from core.permissions import IsProfessional, Roles, role_can, role_can_write, role_sections
from patients.access import grant_allows_full, has_active_grant

from .models import AccessRequest, Appointment, Assurance, DossierMedical, Patient
from .serializers import (
    AssuranceSerializer,
    DossierMedicalSerializer,
    PatientDetailSerializer,
    PatientListSerializer,
    PatientWriteSerializer,
    UrgenceSerializer,
)

# Rôles autorisés à demander le dossier complet (hors admin déjà exempt).
FULL_ACCESS_REQUEST_ROLES = frozenset(
    {
        Roles.MEDECIN,
        Roles.INFIRMIER,
        Roles.PHARMACIEN,
        Roles.LABORANTIN,
        Roles.RECEPTIONNISTE,
    }
)


def _filter_patient_payload(data: dict, role: str) -> dict:
    """Retire les blocs dossier non autorisés selon le rôle (RBAC)."""
    sections = role_sections(role)
    out = dict(data)
    out["access"] = {
        "role": role,
        "sections": sorted(sections),
        "write": {
            "historique": role_can_write(role, "historique"),
            "ordonnances": role_can_write(role, "ordonnances"),
            "dispenser": role_can_write(role, "dispenser"),
            "payer": role_can_write(role, "dispenser"),
            "examens": role_can_write(role, "examens"),
            "constantes": role_can_write(role, "constantes"),
            "assurance": role_can_write(role, "assurance"),
            "dossier": role_can_write(role, "dossier"),
            "demographie": role_can_write(role, "demographie"),
        },
    }
    if not role_can(role, "dossier"):
        out.pop("dossier", None)
    if not role_can(role, "assurance"):
        out.pop("assurance", None)
    if role in (Roles.LABORANTIN, Roles.AMBULANCIER, Roles.RECEPTIONNISTE, Roles.PHARMACIEN):
        for key in ("telephone", "email", "created_at"):
            if role == Roles.RECEPTIONNISTE and key == "telephone":
                continue
            out.pop(key, None)
    return out


def _urgence_only_payload(patient, role: str, request=None) -> dict:
    ctx = {"request": request} if request else {}
    data = PatientListSerializer(patient, context=ctx).data
    data["urgence"] = UrgenceSerializer(patient, context=ctx).data
    if role_can(role, "assurance"):
        assurance = getattr(patient, "assurance", None)
        data["assurance"] = AssuranceSerializer(assurance).data if assurance else None
    data["consent"] = {"required": True, "granted": False, "urgence_only": True}
    return _filter_patient_payload(data, role)


def _granted_consent(grant) -> dict:
    expires = getattr(grant, "grant_expires_at", None) if grant else None
    return {
        "required": False,
        "granted": True,
        "access_request_id": grant.id if grant else None,
        "grant_expires_at": expires.isoformat() if expires else None,
        "direct_structure": bool(grant and getattr(grant, "direct_structure", False)),
    }


class PatientViewSet(viewsets.ModelViewSet):
    queryset = Patient.objects.select_related(
        "dossier", "assurance", "user", "hopital_mere", "medecin_traitant"
    ).all()
    permission_classes = [IsProfessional]
    search_fields = ["npi", "nom", "prenom", "telephone"]
    filterset_fields = ["groupe_sanguin", "sexe", "npi_verifie_anip"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PatientWriteSerializer
        if self.action == "list":
            return PatientListSerializer
        return PatientDetailSerializer

    def create(self, request, *args, **kwargs):
        if not role_can_write(request.user.role, "demographie"):
            raise PermissionDenied("Création patient réservée à la réception / admin / médecin.")
        response = super().create(request, *args, **kwargs)
        patient_id = None
        if isinstance(response.data, dict):
            patient_id = response.data.get("id")
        if patient_id:
            from patients.models import Patient as PatientModel

            p = PatientModel.objects.filter(pk=patient_id).first()
            if p:
                log_action(
                    request,
                    "creer_patient",
                    target=p.full_name,
                    patient_npi=p.npi,
                )
                structure = getattr(request.user, "structure_principale", None)
                if structure and not p.hopital_mere_id:
                    p.hopital_mere = structure
                    p.save(update_fields=["hopital_mere", "updated_at"])
                    if isinstance(response.data, dict):
                        response.data["hopital_mere"] = structure.id
                        response.data["hopital_mere_nom"] = structure.nom
            from notifications.services import publish_patient_list

            publish_patient_list(patient_id=patient_id, actor=request.user, kind="created")
        return response

    def update(self, request, *args, **kwargs):
        if not role_can_write(request.user.role, "demographie"):
            raise PermissionDenied("Modification démographique non autorisée pour ce rôle.")
        response = super().update(request, *args, **kwargs)
        pid = kwargs.get("pk")
        if pid:
            from notifications.services import publish_patient_list

            publish_patient_list(patient_id=pid, actor=request.user, kind="updated")
        return response

    def partial_update(self, request, *args, **kwargs):
        # Sans partial=True, un PATCH photo seule exige npi/nom/prénom et répond 400.
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        from patients.hopital import bypasses_consent, etablissement_payload

        patient = self.get_object()
        role = request.user.role
        emergency = request.query_params.get("emergency") in ("1", "true", "yes")
        sid = getattr(request.user, "structure_principale_id", None)

        if bypasses_consent(request.user, patient):
            log_action(
                request,
                "consulter_dossier",
                target=f"{patient.full_name}|structure:{sid or '-'}",
                patient_npi=patient.npi,
            )
            data = PatientDetailSerializer(patient, context=self.get_serializer_context()).data
            data["consent"] = {"required": False, "granted": True, "admin": True}
            data["etablissement"] = etablissement_payload(request.user, patient)
            return Response(_filter_patient_payload(data, role))

        grant = has_active_grant(request.user, patient)
        if not grant_allows_full(grant):
            from patients.access import access_expired_for

            live_emergency = bool(
                grant
                and grant.status == AccessRequest.Status.EMERGENCY_BYPASS
                and grant.has_active_grant
            )
            if live_emergency or emergency or role == Roles.AMBULANCIER:
                if not live_emergency:
                    from patients.access import create_access_request

                    create_access_request(
                        requester=request.user,
                        patient=patient,
                        mode=AccessRequest.Mode.EMERGENCY,
                        reason="Ouverture urgence",
                        emergency=True,
                        request=request,
                    )
                log_action(
                    request, "consulter_urgence", target=patient.full_name, patient_npi=patient.npi
                )
                data = _urgence_only_payload(patient, role, request=request)
                data["consent"] = {
                    "required": True,
                    "granted": False,
                    "emergency": True,
                    "urgence_only": True,
                    "message": "Accès urgence - données critiques uniquement.",
                }
                data["etablissement"] = etablissement_payload(request.user, patient)
                return Response(data)

            expired = access_expired_for(request.user, patient)
            log_action(
                request, "consulter_dossier_bloque", target=patient.full_name, patient_npi=patient.npi
            )
            from patients.hopital import (
                etablissement_payload,
                has_structure_direct_access,
                masked_dossier_payload,
                patient_controls_external_requests,
            )

            pending = (
                AccessRequest.objects.filter(
                    requester=request.user,
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
            if (
                not patient_controls_external_requests(patient)
                and patient.hopital_mere_id
                and not has_structure_direct_access(request.user, patient)
            ):
                payload = masked_dossier_payload(patient, pending, user=request.user)
                if expired and not pending:
                    payload["detail"] = "Cet accès a expiré"
                    payload["consent"]["expired"] = True
                    payload["consent"]["message"] = "Cet accès a expiré"
                    payload["consent"]["can_request"] = True
                return Response(payload, status=status.HTTP_202_ACCEPTED)

            data = _urgence_only_payload(patient, role, request=request)
            can_request = role in FULL_ACCESS_REQUEST_ROLES
            controls = patient_controls_external_requests(patient)
            if pending:
                waiting_prior = pending.status == AccessRequest.Status.PENDING_PRIOR
                waiting_hopital = pending.route == AccessRequest.Route.HOPITAL_MERE
                consent = {
                    "required": True,
                    "granted": False,
                    "pending": True,
                    "awaiting_prior": waiting_prior,
                    "awaiting_hopital_mere": waiting_hopital,
                    "route": pending.route,
                    "can_request": can_request,
                    "access_request_id": pending.id,
                    "message": (
                        "Le patient a accepté. En attente de confirmation de l'ancien médecin…"
                        if waiting_prior
                        else (
                            "Demande envoyée à l'hôpital mère. Le dossier reste fermé."
                            if waiting_hopital
                            else "En attente de confirmation patient…"
                        )
                    ),
                }
            else:
                consent = {
                    "required": True,
                    "granted": False,
                    "pending": False,
                    "can_request": can_request,
                    "route": "patient" if controls else "hopital_mere",
                    "needs_duration": not controls and bool(patient.hopital_mere_id),
                    "message": (
                        "Infos de base uniquement. Demandez l'accès complet pour le dossier."
                        if controls
                        else "Dossier fermé. Indiquez la durée : la demande part à l'hôpital mère."
                    ),
                }
            if expired and not pending:
                consent["expired"] = True
                consent["message"] = "Cet accès a expiré"
            data["etablissement"] = etablissement_payload(request.user, patient)
            return Response(
                {
                    **data,
                    "detail": consent.get("message") if expired and not pending else "Consentement requis pour le dossier complet.",
                    "consent": consent,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        action_name = (
            "ouverture_dossier" if getattr(grant, "direct_structure", False) else "consulter_dossier"
        )
        sid = getattr(request.user, "structure_principale_id", None)
        log_action(
            request,
            action_name,
            target=f"{patient.full_name}|structure:{sid or '-'}",
            patient_npi=patient.npi,
        )
        from patients.hopital import etablissement_payload

        if role in (Roles.LABORANTIN, Roles.AMBULANCIER, Roles.RECEPTIONNISTE, Roles.PHARMACIEN):
            data = PatientListSerializer(patient, context=self.get_serializer_context()).data
            data["urgence"] = UrgenceSerializer(patient).data
            if role_can(role, "assurance"):
                assurance = getattr(patient, "assurance", None)
                data["assurance"] = AssuranceSerializer(assurance).data if assurance else None
            data["consent"] = _granted_consent(grant)
            data["etablissement"] = etablissement_payload(request.user, patient)
            return Response(_filter_patient_payload(data, role))
        data = PatientDetailSerializer(patient, context=self.get_serializer_context()).data
        data["consent"] = _granted_consent(grant)
        data["etablissement"] = etablissement_payload(request.user, patient)
        return Response(_filter_patient_payload(data, role))

    @action(detail=False, methods=["get"])
    def search(self, request):
        npi = request.query_params.get("npi")
        nom = request.query_params.get("nom")
        date = request.query_params.get("date_naissance")
        qs = self.get_queryset()
        if npi:
            qs = qs.filter(npi__iexact=npi.strip())
        elif nom:
            qs = qs.filter(Q(nom__icontains=nom) | Q(prenom__icontains=nom))
            if date:
                qs = qs.filter(date_naissance=date)
        else:
            return Response(
                {"detail": "Fournir un NPI ou un nom."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        log_action(request, "recherche_patient", target=npi or nom)
        from patients.hopital import (
            bypasses_consent,
            has_structure_direct_access,
            patient_controls_external_requests,
        )

        patients = list(qs[:50])
        rows = PatientListSerializer(
            patients, many=True, context=self.get_serializer_context()
        ).data
        for row, patient in zip(rows, patients):
            if bypasses_consent(request.user, patient) or patient_controls_external_requests(patient):
                continue
            if has_structure_direct_access(request.user, patient):
                continue
            if has_active_grant(request.user, patient):
                continue
            row["groupe_sanguin"] = ""
            row["electrophorese"] = ""
            row["dossier_masque"] = True
        return Response(rows)

    @action(detail=True, methods=["post"], url_path="emettre-carte")
    def emettre_carte(self, request, pk=None):
        """Accueil ou admin de l'hôpital mère : émet la DotoCard si le dossier est prêt."""
        from cards.models import DodoCard
        from cards.serializers import DodoCardSerializer
        from cards.views import _active_card, _ensure_ready_for_issue
        from patients.hopital import DESK_ROLES, user_structure_id

        patient = self.get_object()
        sid = user_structure_id(request.user)
        if request.user.role not in DESK_ROLES or not sid or sid != patient.hopital_mere_id:
            raise PermissionDenied(
                "Seuls l'accueil et l'admin de l'hôpital mère peuvent émettre la DotoCard."
            )
        active = _active_card(patient)
        if active:
            return Response(DodoCardSerializer(active).data)
        blocked = _ensure_ready_for_issue(patient)
        if blocked is not None:
            return blocked
        card = DodoCard.issue(patient)
        log_action(request, "emettre_dodocard", target=f"card:{card.id}", patient_npi=patient.npi)
        return Response(DodoCardSerializer(card).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="photo",
        parser_classes=[MultiPartParser, FormParser],
    )
    def photo(self, request, pk=None):
        """Photo d'identité du patient (accueil). Même contrôle que la photo de compte."""
        if not role_can_write(request.user.role, "demographie"):
            raise PermissionDenied("Modification démographique non autorisée pour ce rôle.")
        from accounts.photo_utils import patient_photo_url, prepare_identity_photo

        patient = self.get_object()
        uploaded = request.FILES.get("photo") or request.FILES.get("file") or request.FILES.get("image")
        if not uploaded:
            return Response(
                {"detail": "Fichier « photo » requis (multipart)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            raw, ext = prepare_identity_photo(uploaded)
        except DjangoValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return Response({"detail": msg}, status=status.HTTP_400_BAD_REQUEST)
        stamp = int(timezone.now().timestamp())
        filename = f"patient_{patient.id}_{stamp}.{ext}"
        try:
            if patient.photo:
                patient.photo.delete(save=False)
            patient.photo.save(filename, ContentFile(raw), save=True)
        except OSError:
            return Response(
                {"detail": "Enregistrement de la photo impossible."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        log_action(request, "upload_photo_patient", target=patient.full_name, patient_npi=patient.npi)
        return Response(
            {
                "id": patient.id,
                "photo_url": patient_photo_url(patient, request=request),
            }
        )

    @action(detail=True, methods=["get"], url_path="medecins-referents")
    def medecins_referents(self, request, pk=None):
        from patients.hopital import DESK_ROLES, user_structure_id

        patient = self.get_object()
        sid = user_structure_id(request.user)
        if request.user.role not in DESK_ROLES or not sid or sid != patient.hopital_mere_id:
            raise PermissionDenied("Réservé à l'accueil de l'hôpital mère.")
        from django.contrib.auth import get_user_model

        User = get_user_model()
        qs = User.objects.filter(
            role=Roles.MEDECIN, actif=True, structure_principale_id=patient.hopital_mere_id
        ).order_by("last_name", "first_name")
        return Response(
            [
                {"id": u.id, "full_name": u.get_full_name() or u.username}
                for u in qs[:100]
            ]
        )

    @action(detail=True, methods=["post"], url_path="medecin-referent")
    def medecin_referent(self, request, pk=None):
        from django.contrib.auth import get_user_model

        from patients.hopital import DESK_ROLES, user_structure_id

        patient = self.get_object()
        sid = user_structure_id(request.user)
        if request.user.role not in DESK_ROLES or not sid or sid != patient.hopital_mere_id:
            raise PermissionDenied("Réservé à l'accueil de l'hôpital mère.")
        raw = request.data.get("medecin_id")
        if raw in (None, "", 0, "0"):
            patient.medecin_traitant = None
            patient.save(update_fields=["medecin_traitant", "updated_at"])
            return Response({"medecin_referent_id": None, "medecin_referent_nom": None})
        User = get_user_model()
        medecin = User.objects.filter(
            pk=raw,
            role=Roles.MEDECIN,
            actif=True,
            structure_principale_id=patient.hopital_mere_id,
        ).first()
        if medecin is None:
            return Response(
                {"detail": "Médecin introuvable dans l'hôpital mère."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        patient.medecin_traitant = medecin
        patient.save(update_fields=["medecin_traitant", "updated_at"])
        log_action(
            request,
            "designer_medecin_referent",
            target=medecin.get_full_name() or medecin.username,
            patient_npi=patient.npi,
        )
        return Response(
            {
                "medecin_referent_id": medecin.id,
                "medecin_referent_nom": medecin.get_full_name() or medecin.username,
            }
        )

    @action(detail=False, methods=["get"])
    def suggestions(self, request):
        """
        Suggestions patients pour création RDV.
        Priorité : patients déjà vus (RDV récents / accès accordés), puis recherche q.
        """
        q = (request.query_params.get("q") or "").strip()
        try:
            limit = min(max(int(request.query_params.get("limit") or 20), 1), 50)
        except (TypeError, ValueError):
            limit = 20

        user = request.user
        structure = getattr(user, "structure_principale", None)

        recent_appt = Appointment.objects.all()
        if user.role == Roles.ADMIN:
            pass
        elif structure:
            recent_appt = recent_appt.filter(
                Q(structure=structure) | Q(professionnel=user) | Q(created_by=user)
            )
        else:
            recent_appt = recent_appt.filter(Q(professionnel=user) | Q(created_by=user))

        seen_ids = list(
            recent_appt.values("patient_id")
            .annotate(last=Max("debut"))
            .order_by("-last")
            .values_list("patient_id", flat=True)[:limit]
        )

        access_ids: list[int] = []
        from patients.access import expire_stale_requests

        expire_stale_requests(requester=user)
        for req in (
            AccessRequest.objects.filter(
                requester=user,
                status__in=(
                    AccessRequest.Status.APPROVED,
                    AccessRequest.Status.EMERGENCY_BYPASS,
                ),
            )
            .order_by("-created_at")[: limit * 3]
        ):
            if req.status == AccessRequest.Status.EMERGENCY_BYPASS:
                continue
            if not req.has_active_grant:
                continue
            if req.patient_id and req.patient_id not in access_ids:
                access_ids.append(req.patient_id)
            if len(access_ids) >= limit:
                break

        priority_ids: list[int] = []
        for pid in list(seen_ids) + list(access_ids):
            if pid and pid not in priority_ids:
                priority_ids.append(pid)

        qs = self.get_queryset()
        if q:
            qs = qs.filter(
                Q(npi__icontains=q)
                | Q(nom__icontains=q)
                | Q(prenom__icontains=q)
                | Q(telephone__icontains=q)
            )

        results: list[dict] = []
        ctx = self.get_serializer_context()
        if priority_ids:
            priority_qs = qs.filter(id__in=priority_ids)
            by_id = {p.id: p for p in priority_qs}
            for pid in priority_ids:
                p = by_id.get(pid)
                if p:
                    row = PatientListSerializer(p, context=ctx).data
                    row["suggestion_reason"] = "recent"
                    results.append(row)
                if len(results) >= limit:
                    break

        if len(results) < limit:
            exclude = {r["id"] for r in results}
            extra = qs.exclude(id__in=exclude).order_by("nom", "prenom")[: limit - len(results)]
            for p in extra:
                row = PatientListSerializer(p, context=ctx).data
                row["suggestion_reason"] = "search" if q else "directory"
                results.append(row)

        return Response(results)

    @action(detail=True, methods=["get"])
    def urgence(self, request, pk=None):
        patient = self.get_object()
        return Response(UrgenceSerializer(patient, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def verify_anip(self, request, pk=None):
        from core.providers import get_anip_client

        patient = self.get_object()
        result = get_anip_client().verify_npi(
            patient.npi, nom=patient.nom, prenom=patient.prenom
        )
        patient.npi_verifie_anip = result.verifie
        patient.save(update_fields=["npi_verifie_anip"])
        log_action(request, "verification_anip", target=patient.npi, patient_npi=patient.npi)
        return Response(
            {
                "npi": patient.npi,
                "verifie": result.verifie,
                "source": result.source,
                "nom": result.nom,
                "prenom": result.prenom,
            }
        )

    @action(detail=True, methods=["get", "put", "patch", "delete"])
    def assurance(self, request, pk=None):
        patient = self.get_object()
        instance = getattr(patient, "assurance", None)
        if request.method == "GET":
            if instance is None:
                return Response({"detail": "Aucune assurance."}, status=status.HTTP_404_NOT_FOUND)
            return Response(AssuranceSerializer(instance).data)
        if not role_can_write(request.user.role, "assurance"):
            raise PermissionDenied("Modification réservée au médecin, à la réception ou à l'admin.")
        from notifications.services import publish_insurance_updated

        if request.method == "DELETE":
            if instance is None:
                return Response({"detail": "Aucune assurance."}, status=status.HTTP_404_NOT_FOUND)
            instance.delete()
            log_action(
                request,
                "retirer_assurance",
                target=patient.full_name,
                patient_npi=patient.npi,
            )
            publish_insurance_updated(patient, actor=request.user, kind="removed")
            return Response(status=status.HTTP_204_NO_CONTENT)

        created = instance is None
        serializer = AssuranceSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = serializer.save(patient=patient)
        log_action(
            request,
            "assurance_created" if created else "assurance_updated",
            target=patient.full_name,
            patient_npi=patient.npi,
        )
        publish_insurance_updated(
            patient,
            actor=request.user,
            kind="created" if created else "updated",
            assurance=saved,
        )
        return Response(serializer.data)

    @action(detail=True, methods=["put", "patch"])
    def dossier(self, request, pk=None):
        patient = self.get_object()
        if not role_can_write(request.user.role, "dossier"):
            raise PermissionDenied("Modification non autorisée pour ce rôle.")
        dossier, _ = DossierMedical.objects.get_or_create(patient=patient)
        serializer = DossierMedicalSerializer(dossier, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MonDossierView(viewsets.ViewSet):
    """Vue self-service pour le patient connecté (app mobile DotoPlus)."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response(
                {"detail": "Aucun dossier patient associé à ce compte."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = PatientDetailSerializer(patient, context={"request": request}).data
        data["profile_complete"] = bool(
            patient.date_naissance
            and patient.nom
            and patient.prenom
            and (patient.photo or (patient.user and patient.user.photo))
        )
        return Response(data)

    def partial_update(self, request):
        """
        Patient : profil d'inscription / contacts / seeds allergies & assurance.
        Pas de modification libre du diagnostic clinique.
        """
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response(
                {"detail": "Aucun dossier patient associé à ce compte."},
                status=status.HTTP_404_NOT_FOUND,
            )
        allowed = {
            "telephone",
            "email",
            "contact_urgence_nom",
            "contact_urgence_lien",
            "tel_urgence",
            "date_naissance",
            "lieu_naissance",
            "sexe",
            "groupe_sanguin",
            "electrophorese",
            "nom",
            "prenom",
            "nom_pere",
            "nom_mere",
            "adresse_commune",
            "adresse_quartier",
        }
        data = {k: v for k, v in request.data.items() if k in allowed}
        allergies = request.data.get("allergies")
        maladies = request.data.get("maladies_chroniques")
        antecedents = request.data.get("antecedents")
        assurance_data = request.data.get("assurance")

        if (
            not data
            and allergies is None
            and maladies is None
            and antecedents is None
            and assurance_data is None
        ):
            return Response(
                {
                    "detail": "Aucun champ autorisé. Le patient ne peut pas modifier les données médicales cliniques."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        for k, v in data.items():
            setattr(patient, k, v)
        if data:
            patient.save(update_fields=list(data.keys()) + ["updated_at"])

        if allergies is not None or maladies is not None or antecedents is not None:
            dossier, _ = DossierMedical.objects.get_or_create(patient=patient)
            if allergies is not None:
                dossier.allergies = allergies if isinstance(allergies, list) else []
            if maladies is not None:
                dossier.maladies_chroniques = maladies if isinstance(maladies, list) else []
            if antecedents is not None:
                dossier.antecedents = antecedents if isinstance(antecedents, str) else ""
            dossier.save()

        if isinstance(assurance_data, dict) and assurance_data:
            instance = getattr(patient, "assurance", None)
            created = instance is None
            ser = AssuranceSerializer(instance, data=assurance_data, partial=True)
            ser.is_valid(raise_exception=True)
            saved = ser.save(patient=patient)
            from notifications.services import publish_insurance_updated

            publish_insurance_updated(
                patient,
                actor=request.user,
                kind="created" if created else "updated",
                assurance=saved,
            )

        out = PatientDetailSerializer(patient, context={"request": request}).data
        out["profile_complete"] = bool(
            patient.date_naissance
            and patient.nom
            and patient.prenom
            and (patient.photo or (patient.user and patient.user.photo))
        )
        return Response(out)

    def historique(self, request):
        """Patient : consultations (synthèse), ses ordonnances et ses examens.

        Les notes privées du médecin restent hors de cette réponse. Ordonnances,
        résultats et bons sont limités au dossier du compte connecté.
        """
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response({"detail": "Aucun dossier."}, status=status.HTTP_404_NOT_FOUND)

        qs = (
            patient.consultations.filter(annule=False)
            .select_related("structure", "medecin")
            .order_by("-date")[:50]
        )
        syntheses = []
        for c in qs:
            med = ""
            if c.medecin:
                med = c.medecin.get_full_name() or c.medecin.username or ""
                if med and not med.lower().startswith("dr"):
                    med = f"Dr {med}"
            synthese = (c.synthese_patient or "").strip()
            if not synthese:
                bits = [c.get_type_display() or "Consultation"]
                if c.specialite:
                    bits.append(c.specialite)
                if med:
                    bits.append(f"avec {med}")
                if c.structure:
                    bits.append(f"à {c.structure.nom}")
                if c.motif:
                    bits.append(f"Motif : {c.motif}.")
                synthese = " ".join(bits)
            syntheses.append(
                {
                    "id": c.id,
                    "date": c.date.isoformat() if c.date else None,
                    "type": c.type,
                    "type_label": c.get_type_display(),
                    "specialite": c.specialite,
                    "motif": c.motif,
                    "medecin_nom": med,
                    "structure_nom": c.structure.nom if c.structure else "",
                    "titre": (
                        " - ".join(
                            p
                            for p in [
                                (c.specialite or "").strip() or "Consultation",
                                med,
                                c.structure.nom if c.structure else "",
                                c.date.strftime("%d/%m/%Y") if c.date else "",
                            ]
                            if p
                        )
                    ),
                    "synthese": synthese,
                    "has_synthese": bool((c.synthese_patient or "").strip()),
                }
            )

        from medical.serializers import (
            BonExamenSerializer,
            ExamenSerializer,
            OrdonnanceSerializer,
        )

        ctx = {"request": request}
        ordo_qs = (
            patient.ordonnances.select_related("medecin", "patient", "structure")
            .prefetch_related("medicaments")
            .order_by("-date", "-id")[:50]
        )
        # Même filtre que le hub : les examens annulés ne figurent pas dans la file.
        exam_qs = (
            patient.examens.select_related(
                "laborantin", "patient", "medecin_prescripteur", "bon"
            )
            .filter(annule=False)
            .order_by("-date", "-id")[:50]
        )
        bon_qs = (
            patient.bons_examen.select_related(
                "patient", "medecin", "structure", "laboratoire"
            )
            .prefetch_related("lignes", "resultats")
            .order_by("-created_at", "-id")[:50]
        )
        ordonnances = OrdonnanceSerializer(ordo_qs, many=True, context=ctx).data
        examens = ExamenSerializer(exam_qs, many=True, context=ctx).data
        bons = BonExamenSerializer(bon_qs, many=True, context=ctx).data

        return Response(
            {
                "syntheses": syntheses,
                "consultations": syntheses,
                "ordonnances": ordonnances,
                "examens": examens,
                "bons_examen": bons,
                "message": (
                    "Vous voyez la date et le type de chaque visite, "
                    "ainsi que la synthèse rédigée pour vous par le médecin le cas échéant."
                ),
            }
        )

    def mon_assurance(self, request):
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response({"detail": "Aucun dossier."}, status=status.HTTP_404_NOT_FOUND)
        instance = getattr(patient, "assurance", None)
        if request.method == "GET":
            if instance is None:
                return Response({"detail": "Aucune assurance."}, status=status.HTTP_404_NOT_FOUND)
            return Response(AssuranceSerializer(instance).data)
        from notifications.services import publish_insurance_updated

        if request.method == "DELETE":
            if instance is None:
                return Response({"detail": "Aucune assurance."}, status=status.HTTP_404_NOT_FOUND)
            instance.delete()
            publish_insurance_updated(patient, actor=request.user, kind="removed")
            return Response(status=status.HTTP_204_NO_CONTENT)
        created = instance is None
        ser = AssuranceSerializer(instance, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        saved = ser.save(patient=patient)
        publish_insurance_updated(
            patient,
            actor=request.user,
            kind="created" if created else "updated",
            assurance=saved,
        )
        return Response(ser.data)
