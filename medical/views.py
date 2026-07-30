from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.utils import log_action
from core.permissions import ReadOnlyOrRole, Roles, SECTION_READ_ROLES
from notifications.models import Notification
from notifications.services import notify_patient_dossier_change
from patients.access import grant_allows_full, has_active_grant

from .models import Consultation, ConstanteVitale, Examen, Ordonnance
from .serializers import (
    ConstanteVitaleSerializer,
    ConsultationSerializer,
    ExamenSerializer,
    OrdonnanceSerializer,
)


def _assert_section_read(user, section: str):
    if getattr(user, "role", None) == Roles.PATIENT:
        return
    allowed = SECTION_READ_ROLES.get(section, ())
    if user.role not in allowed and user.role != Roles.ADMIN:
        raise PermissionDenied(f"Lecture « {section} » non autorisée pour ce rôle.")


def _assert_patient_consent(request, patient_id):
    """Bloque lecture section médicale sans grant (sauf admin / patient soi-même)."""
    user = request.user
    if getattr(user, "role", None) == Roles.PATIENT:
        return
    if getattr(user, "role", None) == Roles.ADMIN:
        return
    if not patient_id:
        return
    from patients.models import Patient

    patient = Patient.objects.filter(pk=patient_id).first()
    if patient is None:
        raise PermissionDenied("Patient introuvable.")
    grant = has_active_grant(user, patient)
    if not grant_allows_full(grant):
        if user.role == Roles.AMBULANCIER:
            return  # urgence : constantes autorisées
        raise PermissionDenied(
            "Consentement patient requis. Demandez l'accès puis attendez la confirmation."
        )


class PatientScopedMixin:
    """Filtre par ?patient=<id> ; les patients ne voient que leur propre dossier."""

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "role", None) == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            return qs.filter(patient=patient) if patient else qs.none()
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            _assert_patient_consent(self.request, patient_id)
            qs = qs.filter(patient_id=patient_id)
        return qs


class RoleOrPatientRead(IsAuthenticated):
    """Pros (ReadOnlyOrRole) OU patient authentifié en lecture seule."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.role == Roles.PATIENT:
            return request.method in ("GET", "HEAD", "OPTIONS")
        return ReadOnlyOrRole().has_permission(request, view)


class ConsultationViewSet(PatientScopedMixin, viewsets.ModelViewSet):
    queryset = Consultation.objects.select_related("structure", "medecin").all()
    serializer_class = ConsultationSerializer
    permission_classes = [RoleOrPatientRead]
    write_roles = (Roles.MEDECIN, Roles.ADMIN)
    filterset_fields = ["patient", "type", "structure"]

    def get_permissions(self):
        if self.action == "annuler":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "role", None) != Roles.PATIENT:
            _assert_section_read(user, "historique")
        if self.request.query_params.get("inclure_annules") not in ("1", "true", "True"):
            qs = qs.filter(annule=False)
        return qs

    def perform_create(self, serializer):
        consultation = serializer.save(medecin=self.request.user)
        med = self.request.user.get_full_name() or self.request.user.username
        notify_patient_dossier_change(
            consultation.patient,
            title="Nouvelle consultation",
            body=f"Une consultation a été ajoutée à votre dossier par {med}.",
            notif_type=Notification.Type.DOSSIER_UPDATED,
            event_type="dossier_updated",
            section="dossier",
            payload={"kind": "consultation", "consultation_id": consultation.id},
            actor=self.request.user,
        )

    @action(detail=True, methods=["post"])
    def annuler(self, request, pk=None):
        """Médecin / admin : soft-cancel d'une consultation (erreur de saisie)."""
        if request.user.role not in (Roles.MEDECIN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au médecin."}, status=status.HTTP_403_FORBIDDEN
            )
        consultation = self.get_object()
        if request.user.role == Roles.MEDECIN and consultation.medecin_id not in (
            None,
            request.user.id,
        ):
            return Response(
                {"detail": "Vous ne pouvez annuler que vos consultations."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if consultation.annule:
            return Response(
                {"detail": "Consultation déjà annulée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        consultation.annule = True
        consultation.save(update_fields=["annule"])
        log_action(
            request,
            "annuler_consultation",
            target=f"consultation:{pk}",
            patient_npi=consultation.patient.npi,
        )
        notify_patient_dossier_change(
            consultation.patient,
            title="Consultation annulée",
            body="Une consultation de votre dossier a été annulée.",
            notif_type=Notification.Type.DOSSIER_UPDATED,
            event_type="dossier_updated",
            section="dossier",
            payload={"kind": "consultation_annulee", "consultation_id": consultation.id},
            actor=request.user,
        )
        return Response(ConsultationSerializer(consultation).data)


class OrdonnanceViewSet(PatientScopedMixin, viewsets.ModelViewSet):
    queryset = (
        Ordonnance.objects.select_related("medecin", "patient", "structure")
        .prefetch_related("medicaments")
        .all()
    )
    serializer_class = OrdonnanceSerializer
    permission_classes = [RoleOrPatientRead]
    write_roles = (Roles.MEDECIN, Roles.ADMIN)
    filterset_fields = ["patient", "statut"]

    def get_permissions(self):
        if self.action in ("dispenser", "annuler", "annuler_dispense"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        # Ordonnance portable : prescrite chez le médecin, dispensable dans
        # n'importe quelle pharmacie (structure = lieu de prescription / audit).
        #
        # Pharmacien :
        # - liste : obligatoirement filtrée par ?patient= (pas de file « structure »)
        # - lecture ordo pour dispense : sans consentement dossier complet
        # - retrieve / dispenser : accès par id (patient au comptoir)
        qs = (
            Ordonnance.objects.select_related("medecin", "patient", "structure")
            .prefetch_related("medicaments")
            .all()
        )
        user = self.request.user
        if getattr(user, "role", None) == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            return qs.filter(patient=patient) if patient else qs.none()

        _assert_section_read(user, "ordonnances")
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            if user.role not in (Roles.PHARMACIEN, Roles.ADMIN):
                _assert_patient_consent(self.request, patient_id)
            qs = qs.filter(patient_id=patient_id)
        elif user.role == Roles.PHARMACIEN and self.action == "list":
            # Pas de file globale ni par structure du médecin.
            qs = qs.none()
        return qs

    def perform_create(self, serializer):
        # structure = lieu de prescription (audit), pas le lieu de dispense.
        structure = getattr(self.request.user, "structure_principale", None)
        ordonnance = serializer.save(medecin=self.request.user, structure=structure)
        log_action(
            self.request, "creer_ordonnance",
            target=f"ordonnance:{ordonnance.id}", patient_npi=ordonnance.patient.npi,
        )
        med = self.request.user.get_full_name() or self.request.user.username
        notify_patient_dossier_change(
            ordonnance.patient,
            title="Nouvelle ordonnance",
            body=f"Une ordonnance a été prescrite par {med}.",
            notif_type=Notification.Type.ORDONNANCE,
            event_type="ordonnance",
            section="ordonnances",
            payload={"kind": "ordonnance", "ordonnance_id": ordonnance.id},
            actor=self.request.user,
        )

    @action(detail=True, methods=["post"])
    def dispenser(self, request, pk=None):
        """Marquer une ordonnance comme dispensée — pharmacien (CDC §3.5, §5.5)."""
        if request.user.role not in (Roles.PHARMACIEN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au pharmacien."}, status=status.HTTP_403_FORBIDDEN
            )
        ordonnance = self.get_object()
        if ordonnance.statut == Ordonnance.Statut.ANNULEE:
            return Response(
                {"detail": "Ordonnance annulée — impossible de dispenser."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if ordonnance.statut == Ordonnance.Statut.DISPENSEE:
            return Response(
                {"detail": "Déjà dispensée."}, status=status.HTTP_400_BAD_REQUEST
            )
        ordonnance.statut = Ordonnance.Statut.DISPENSEE
        ordonnance.dispensee_le = timezone.now()
        ordonnance.dispensee_par = request.user
        ordonnance.save(update_fields=["statut", "dispensee_le", "dispensee_par"])
        log_action(request, "dispenser_ordonnance", target=f"ordonnance:{pk}",
                   patient_npi=ordonnance.patient.npi)
        notify_patient_dossier_change(
            ordonnance.patient,
            title="Ordonnance dispensée",
            body="Votre ordonnance a été délivrée en pharmacie.",
            notif_type=Notification.Type.ORDONNANCE,
            event_type="ordonnance",
            section="ordonnances",
            payload={"kind": "ordonnance_dispensee", "ordonnance_id": ordonnance.id},
            actor=request.user,
        )
        return Response(OrdonnanceSerializer(ordonnance).data)

    @action(detail=True, methods=["post"])
    def annuler(self, request, pk=None):
        """Médecin / admin : annuler une ordonnance encore active (erreur de saisie)."""
        if request.user.role not in (Roles.MEDECIN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au médecin."}, status=status.HTTP_403_FORBIDDEN
            )
        ordonnance = self.get_object()
        if request.user.role == Roles.MEDECIN and ordonnance.medecin_id not in (
            None,
            request.user.id,
        ):
            return Response(
                {"detail": "Vous ne pouvez annuler que vos ordonnances."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if ordonnance.statut != Ordonnance.Statut.ACTIVE:
            return Response(
                {"detail": "Seule une ordonnance active peut être annulée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ordonnance.statut = Ordonnance.Statut.ANNULEE
        ordonnance.save(update_fields=["statut"])
        log_action(
            request,
            "annuler_ordonnance",
            target=f"ordonnance:{pk}",
            patient_npi=ordonnance.patient.npi,
        )
        return Response(OrdonnanceSerializer(ordonnance).data)

    @action(detail=True, methods=["post"], url_path="annuler-dispense")
    def annuler_dispense(self, request, pk=None):
        """Pharmacien (auteur de la dispense) / admin : réouvrir une ordonnance dispensée."""
        if request.user.role not in (Roles.PHARMACIEN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au pharmacien."}, status=status.HTTP_403_FORBIDDEN
            )
        ordonnance = self.get_object()
        if ordonnance.statut != Ordonnance.Statut.DISPENSEE:
            return Response(
                {"detail": "Seule une ordonnance dispensée peut être réouverte."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            request.user.role == Roles.PHARMACIEN
            and ordonnance.dispensee_par_id not in (None, request.user.id)
        ):
            return Response(
                {"detail": "Vous ne pouvez annuler que vos propres dispenses."},
                status=status.HTTP_403_FORBIDDEN,
            )
        ordonnance.statut = Ordonnance.Statut.ACTIVE
        ordonnance.dispensee_le = None
        ordonnance.dispensee_par = None
        ordonnance.save(update_fields=["statut", "dispensee_le", "dispensee_par"])
        log_action(
            request,
            "annuler_dispense_ordonnance",
            target=f"ordonnance:{pk}",
            patient_npi=ordonnance.patient.npi,
        )
        return Response(OrdonnanceSerializer(ordonnance).data)


class ExamenViewSet(PatientScopedMixin, viewsets.ModelViewSet):
    queryset = Examen.objects.select_related("laborantin", "patient").all()
    serializer_class = ExamenSerializer
    permission_classes = [RoleOrPatientRead]
    write_roles = (Roles.LABORANTIN, Roles.ADMIN)
    filterset_fields = ["patient", "categorie", "statut"]

    def get_permissions(self):
        if self.action in ("upload_fichier", "annuler"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        from django.db.models import Q

        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "role", None) != Roles.PATIENT:
            _assert_section_read(user, "examens")
        if self.request.query_params.get("inclure_annules") not in ("1", "true", "True"):
            qs = qs.filter(annule=False)
        # Filtres optionnels labo : examens incomplets
        if self.request.query_params.get("sans_fichier") in ("1", "true", "True"):
            qs = qs.filter(Q(fichier="") | Q(fichier__isnull=True))
        if self.request.query_params.get("sans_resultat") in ("1", "true", "True"):
            qs = qs.filter(Q(resultat_texte="") | Q(resultat_texte__isnull=True))
        return qs

    def get_parsers(self):
        from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

        return [MultiPartParser(), FormParser(), JSONParser()]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def perform_create(self, serializer):
        examen = serializer.save(laborantin=self.request.user)
        log_action(self.request, "upload_examen", target=examen.type_examen,
                   patient_npi=examen.patient.npi)
        notify_patient_dossier_change(
            examen.patient,
            title="Nouvel examen",
            body=f"Résultat disponible : {examen.type_examen or 'examen'}.",
            notif_type=Notification.Type.EXAMEN,
            event_type="examen",
            section="examens",
            payload={"kind": "examen", "examen_id": examen.id},
            actor=self.request.user,
        )

    @action(detail=True, methods=["post"])
    def annuler(self, request, pk=None):
        """Laborantin / admin : soft-cancel sans toucher au statut clinique."""
        if request.user.role not in (Roles.LABORANTIN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au laborantin."}, status=status.HTTP_403_FORBIDDEN
            )
        examen = self.get_object()
        if examen.annule:
            return Response(
                {"detail": "Examen déjà annulé."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        examen.annule = True
        examen.save(update_fields=["annule"])
        log_action(
            request,
            "annuler_examen",
            target=examen.type_examen,
            patient_npi=examen.patient.npi,
        )
        return Response(ExamenSerializer(examen, context={"request": request}).data)

    @action(detail=False, methods=["get"])
    def mes_uploads(self, request):
        qs = self.get_queryset().filter(laborantin=request.user)
        return Response(ExamenSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=False, methods=["get"])
    def a_completer(self, request):
        """Examens sans fichier et/ou sans résultat texte."""
        from django.db.models import Q

        qs = self.get_queryset().filter(
            Q(fichier="") | Q(fichier__isnull=True) | Q(resultat_texte="") | Q(resultat_texte__isnull=True)
        )
        return Response(ExamenSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="upload")
    def upload_fichier(self, request, pk=None):
        if request.user.role not in (Roles.LABORANTIN, Roles.ADMIN):
            return Response({"detail": "Réservé au laborantin."}, status=status.HTTP_403_FORBIDDEN)
        examen = self.get_object()
        f = request.FILES.get("fichier")
        if not f:
            return Response({"detail": "Fichier 'fichier' manquant."}, status=status.HTTP_400_BAD_REQUEST)
        examen.fichier = f
        examen.save(update_fields=["fichier"])
        log_action(request, "upload_fichier_examen", target=examen.type_examen,
                   patient_npi=examen.patient.npi)
        notify_patient_dossier_change(
            examen.patient,
            title="Examen mis à jour",
            body=f"Un fichier a été ajouté à : {examen.type_examen or 'examen'}.",
            notif_type=Notification.Type.EXAMEN,
            event_type="examen",
            section="examens",
            payload={"kind": "examen_fichier", "examen_id": examen.id},
            actor=request.user,
        )
        return Response(ExamenSerializer(examen, context={"request": request}).data)


class ConstanteVitaleViewSet(PatientScopedMixin, viewsets.ModelViewSet):
    queryset = ConstanteVitale.objects.select_related("infirmier").all()
    serializer_class = ConstanteVitaleSerializer
    permission_classes = [RoleOrPatientRead]
    write_roles = (Roles.INFIRMIER, Roles.MEDECIN, Roles.AMBULANCIER, Roles.ADMIN)
    filterset_fields = ["patient"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "role", None) != Roles.PATIENT:
            _assert_section_read(user, "constantes")
        return qs

    def perform_create(self, serializer):
        serializer.save(infirmier=self.request.user)
