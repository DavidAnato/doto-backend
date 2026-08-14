from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
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


class PatientViewSet(viewsets.ModelViewSet):
    queryset = Patient.objects.select_related("dossier", "assurance", "user").all()
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
        return self.update(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        patient = self.get_object()
        role = request.user.role
        emergency = request.query_params.get("emergency") in ("1", "true", "yes")

        if role == Roles.ADMIN:
            log_action(request, "consulter_dossier", target=patient.full_name, patient_npi=patient.npi)
            data = PatientDetailSerializer(patient, context=self.get_serializer_context()).data
            data["consent"] = {"required": False, "granted": True, "admin": True}
            return Response(_filter_patient_payload(data, role))

        grant = has_active_grant(request.user, patient)
        if not grant_allows_full(grant):
            if emergency or role == Roles.AMBULANCIER:
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
                    "required": False,
                    "granted": True,
                    "emergency": True,
                    "message": "Accès urgence — consentement patient non requis.",
                }
                return Response(data)

            log_action(
                request, "consulter_dossier_bloque", target=patient.full_name, patient_npi=patient.npi
            )
            data = _urgence_only_payload(patient, role, request=request)
            pending = (
                AccessRequest.objects.filter(
                    requester=request.user,
                    patient=patient,
                    status=AccessRequest.Status.PENDING,
                    expires_at__gt=timezone.now(),
                )
                .order_by("-created_at")
                .first()
            )
            can_request = role in FULL_ACCESS_REQUEST_ROLES
            if pending:
                consent = {
                    "required": True,
                    "granted": False,
                    "pending": True,
                    "can_request": can_request,
                    "access_request_id": pending.id,
                    "message": "En attente de confirmation patient…",
                }
            else:
                consent = {
                    "required": True,
                    "granted": False,
                    "pending": False,
                    "can_request": can_request,
                    "message": "Infos de base uniquement. Demandez l'accès complet pour le dossier.",
                }
            return Response(
                {
                    **data,
                    "detail": "Consentement patient requis pour le dossier complet.",
                    "consent": consent,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        log_action(request, "consulter_dossier", target=patient.full_name, patient_npi=patient.npi)
        if role in (Roles.LABORANTIN, Roles.AMBULANCIER, Roles.RECEPTIONNISTE, Roles.PHARMACIEN):
            data = PatientListSerializer(patient, context=self.get_serializer_context()).data
            data["urgence"] = UrgenceSerializer(patient).data
            if role_can(role, "assurance"):
                assurance = getattr(patient, "assurance", None)
                data["assurance"] = AssuranceSerializer(assurance).data if assurance else None
            data["consent"] = {
                "required": False,
                "granted": True,
                "access_request_id": grant.id if grant else None,
            }
            return Response(_filter_patient_payload(data, role))
        data = PatientDetailSerializer(patient, context=self.get_serializer_context()).data
        data["consent"] = {
            "required": False,
            "granted": True,
            "access_request_id": grant.id if grant else None,
        }
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
        return Response(
            PatientListSerializer(qs, many=True, context=self.get_serializer_context()).data
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

        access_ids = list(
            AccessRequest.objects.filter(
                requester=user,
                status__in=(
                    AccessRequest.Status.APPROVED,
                    AccessRequest.Status.EMERGENCY_BYPASS,
                ),
            )
            .values("patient_id")
            .annotate(last=Max("created_at"))
            .order_by("-last")
            .values_list("patient_id", flat=True)[:limit]
        )

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

    @action(detail=True, methods=["get", "put", "patch"])
    def assurance(self, request, pk=None):
        patient = self.get_object()
        instance = getattr(patient, "assurance", None)
        if request.method == "GET":
            if instance is None:
                return Response({"detail": "Aucune assurance."}, status=status.HTTP_404_NOT_FOUND)
            return Response(AssuranceSerializer(instance).data)
        if not role_can_write(request.user.role, "assurance"):
            raise PermissionDenied("Modification réservée au médecin ou à l'admin.")
        serializer = AssuranceSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(patient=patient)
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
        assurance_data = request.data.get("assurance")

        if not data and allergies is None and maladies is None and assurance_data is None:
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

        if allergies is not None or maladies is not None:
            dossier, _ = DossierMedical.objects.get_or_create(patient=patient)
            if allergies is not None:
                dossier.allergies = allergies if isinstance(allergies, list) else []
            if maladies is not None:
                dossier.maladies_chroniques = maladies if isinstance(maladies, list) else []
            dossier.save()

        if isinstance(assurance_data, dict) and assurance_data:
            instance = getattr(patient, "assurance", None)
            ser = AssuranceSerializer(instance, data=assurance_data, partial=True)
            ser.is_valid(raise_exception=True)
            ser.save(patient=patient)

        out = PatientDetailSerializer(patient, context={"request": request}).data
        out["profile_complete"] = bool(
            patient.date_naissance
            and patient.nom
            and patient.prenom
            and (patient.photo or (patient.user and patient.user.photo))
        )
        return Response(out)

    def historique(self, request):
        """Agrégat consultations + ordonnances + examens pour le patient connecté."""
        patient = getattr(request.user, "patient", None)
        if patient is None:
            return Response({"detail": "Aucun dossier."}, status=status.HTTP_404_NOT_FOUND)
        from medical.serializers import (
            ConsultationSerializer,
            ExamenSerializer,
            OrdonnanceSerializer,
        )

        return Response(
            {
                "consultations": ConsultationSerializer(
                    patient.consultations.select_related("structure", "medecin").all()[:50],
                    many=True,
                ).data,
                "ordonnances": OrdonnanceSerializer(
                    patient.ordonnances.select_related("medecin", "structure")
                    .prefetch_related("medicaments")
                    .all()[:50],
                    many=True,
                ).data,
                "examens": ExamenSerializer(
                    patient.examens.all()[:50],
                    many=True,
                    context={"request": request},
                ).data,
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
        ser = AssuranceSerializer(instance, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save(patient=patient)
        return Response(ser.data)
