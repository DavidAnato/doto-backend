from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.utils import log_action
from core.permissions import ReadOnlyOrRole, Roles, SECTION_READ_ROLES
from notifications.models import Notification
from notifications.services import notify_patient_dossier_change
from patients.access import grant_allows_full, has_active_grant

from .models import BonExamen, Consultation, ConstanteVitale, Examen, Ordonnance
from .serializers import (
    BonExamenSerializer,
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
        from django.utils import timezone
        from rest_framework.exceptions import ValidationError

        from patients.models import Appointment

        user = self.request.user
        data = serializer.validated_data
        attached = set(user.structures.values_list("id", flat=True))
        if user.structure_principale_id:
            attached.add(user.structure_principale_id)
        structure = data.get("structure")
        if structure is None:
            structure = getattr(user, "structure_principale", None)
        elif user.role != Roles.ADMIN and structure.id not in attached:
            raise PermissionDenied("Choisissez une de vos structures rattachées.")
        if structure is None:
            raise ValidationError({"structure": "La structure de santé est obligatoire."})
        specialite = (data.get("specialite") or "").strip() or getattr(user, "specialite", "") or ""
        date = data.get("date") or timezone.now()
        consultation = serializer.save(
            medecin=user, structure=structure, specialite=specialite, date=date
        )
        appt = consultation.appointment
        if appt and appt.statut not in (
            Appointment.Statut.ANNULE,
            Appointment.Statut.ABSENT,
        ):
            appt.statut = Appointment.Statut.TERMINE
            appt.save(update_fields=["statut", "updated_at"])
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
    filterset_fields = ["patient"]

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
        statut = self.request.query_params.get("statut")
        if getattr(user, "role", None) == Roles.PATIENT:
            patient = getattr(user, "patient", None)
            qs = qs.filter(patient=patient) if patient else qs.none()
            if statut in ("payee", "dispensee", "dispense"):
                qs = qs.filter(statut__in=["payee", "dispensee"])
            elif statut:
                qs = qs.filter(statut=statut)
            return qs

        _assert_section_read(user, "ordonnances")
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            if user.role not in (Roles.PHARMACIEN, Roles.ADMIN):
                _assert_patient_consent(self.request, patient_id)
            qs = qs.filter(patient_id=patient_id)
        elif user.role == Roles.PHARMACIEN and self.action == "list":
            qs = qs.none()
        if statut in ("payee", "dispensee", "dispense"):
            qs = qs.filter(statut__in=["payee", "dispensee"])
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
        """Marquer une ordonnance comme payée (pharmacien). URL historique /dispenser/."""
        if request.user.role not in (Roles.PHARMACIEN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au pharmacien."}, status=status.HTTP_403_FORBIDDEN
            )
        ordonnance = self.get_object()
        if ordonnance.statut == Ordonnance.Statut.ANNULEE:
            return Response(
                {"detail": "Ordonnance annulée, impossible de marquer payée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if ordonnance.statut in Ordonnance.PAID_VALUES:
            return Response(
                {"detail": "Déjà payée."}, status=status.HTTP_400_BAD_REQUEST
            )
        ordonnance.statut = Ordonnance.Statut.PAYEE
        ordonnance.dispensee_le = timezone.now()
        ordonnance.dispensee_par = request.user
        ordonnance.save(update_fields=["statut", "dispensee_le", "dispensee_par"])
        log_action(request, "payer_ordonnance", target=f"ordonnance:{pk}",
                   patient_npi=ordonnance.patient.npi)
        notify_patient_dossier_change(
            ordonnance.patient,
            title="Ordonnance payée",
            body="Votre ordonnance a été payée en pharmacie.",
            notif_type=Notification.Type.ORDONNANCE,
            event_type="ordonnance",
            section="ordonnances",
            payload={"kind": "ordonnance_payee", "ordonnance_id": ordonnance.id},
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
        if ordonnance.statut not in Ordonnance.PAID_VALUES:
            return Response(
                {"detail": "Seule une ordonnance payée peut être réouverte."},
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
        extras = {}
        if self.request.user.role in (Roles.LABORANTIN, Roles.ADMIN):
            extras["laborantin"] = self.request.user
        examen = serializer.save(**extras)
        if examen.bon_id:
            if examen.bon.statut in (examen.bon.Statut.DEMANDE, examen.bon.Statut.RECU):
                examen.bon.statut = examen.bon.Statut.EN_COURS
                examen.bon.save(update_fields=["statut", "updated_at"])
            examen.bon.refresh_statut_from_lignes()
        log_action(self.request, "upload_examen", target=examen.type_examen,
                   patient_npi=examen.patient.npi)
        notify_patient_dossier_change(
            examen.patient,
            title="Nouvel examen",
            body=f"Résultat disponible : {examen.type_examen or 'examen'}.",
            notif_type=Notification.Type.EXAMEN,
            event_type="examen",
            section="examens",
            payload={
                "kind": "examen",
                "examen_id": examen.id,
                "bon_id": examen.bon_id,
            },
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


class BonExamenViewSet(PatientScopedMixin, viewsets.ModelViewSet):
    """Bons d'examen : prescription médecin, réalisation laborantin."""

    queryset = (
        BonExamen.objects.select_related("patient", "medecin", "structure", "laboratoire")
        .prefetch_related("lignes", "resultats")
        .all()
    )
    serializer_class = BonExamenSerializer
    permission_classes = [RoleOrPatientRead]
    write_roles = (Roles.MEDECIN, Roles.ADMIN)
    filterset_fields = ["patient", "statut", "laboratoire"]

    def get_permissions(self):
        if self.action in ("recevoir", "demarrer", "cloturer", "deposer_resultat"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "role", None) != Roles.PATIENT:
            _assert_section_read(user, "examens")
        pending = self.request.query_params.get("en_attente")
        if pending in ("1", "true", "True"):
            qs = qs.filter(
                statut__in=[
                    BonExamen.Statut.DEMANDE,
                    BonExamen.Statut.RECU,
                    BonExamen.Statut.EN_COURS,
                ]
            )
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def perform_create(self, serializer):
        user = self.request.user
        structure = getattr(user, "structure_principale", None)
        bon = serializer.save(medecin=user, structure=structure)
        log_action(
            self.request,
            "prescrire_examen",
            target=f"bon:{bon.id}",
            patient_npi=bon.patient.npi,
        )
        med = user.get_full_name() or user.username
        notify_patient_dossier_change(
            bon.patient,
            title="Prescription d'examen",
            body=f"{med} a prescrit un bon d'examen.",
            notif_type=Notification.Type.BON_EXAMEN,
            event_type="examen",
            section="examens",
            payload={"kind": "bon_examen", "bon_id": bon.id},
            actor=user,
        )
        # File labo : notifier les laborantins de la structure destinataire
        if bon.laboratoire_id:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            for lab in User.objects.filter(
                role=Roles.LABORANTIN, actif=True, structures=bon.laboratoire
            ):
                from notifications.services import notify_user

                notify_user(
                    lab,
                    title="Nouveau bon d'examen",
                    body=f"{bon.patient.full_name} - {bon.lignes.count()} examen(s).",
                    type=Notification.Type.BON_EXAMEN,
                    payload={
                        "kind": "bon_examen",
                        "bon_id": bon.id,
                        "patient_id": bon.patient_id,
                        "section": "examens",
                    },
                )

    def _require_labo(self, request):
        if request.user.role not in (Roles.LABORANTIN, Roles.ADMIN):
            return Response(
                {"detail": "Réservé au laborantin."}, status=status.HTTP_403_FORBIDDEN
            )
        return None

    @action(detail=True, methods=["post"])
    def recevoir(self, request, pk=None):
        denied = self._require_labo(request)
        if denied:
            return denied
        bon = self.get_object()
        if bon.statut != BonExamen.Statut.DEMANDE:
            return Response(
                {"detail": "Ce bon n'est pas en statut Demandé."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        bon.statut = BonExamen.Statut.RECU
        bon.save(update_fields=["statut", "updated_at"])
        return Response(BonExamenSerializer(bon, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def demarrer(self, request, pk=None):
        denied = self._require_labo(request)
        if denied:
            return denied
        bon = self.get_object()
        if bon.statut not in (BonExamen.Statut.DEMANDE, BonExamen.Statut.RECU):
            return Response(
                {"detail": "Impossible de démarrer ce bon."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        bon.statut = BonExamen.Statut.EN_COURS
        bon.save(update_fields=["statut", "updated_at"])
        return Response(BonExamenSerializer(bon, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def cloturer(self, request, pk=None):
        denied = self._require_labo(request)
        if denied:
            return denied
        bon = self.get_object()
        bon.statut = BonExamen.Statut.CLOTURE
        bon.save(update_fields=["statut", "updated_at"])
        return Response(BonExamenSerializer(bon, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="deposer-resultat")
    def deposer_resultat(self, request, pk=None):
        """Laborantin : dépose un résultat rattaché au bon ET au dossier patient."""
        denied = self._require_labo(request)
        if denied:
            return denied
        bon = self.get_object()
        from django.utils import timezone

        ligne_id = request.data.get("ligne") or request.data.get("ligne_id")
        ligne = None
        if ligne_id:
            ligne = bon.lignes.filter(pk=ligne_id).first()
        type_examen = (
            request.data.get("type_examen")
            or (ligne.type_examen if ligne else "")
            or "Examen"
        )
        categorie = request.data.get("categorie") or (
            ligne.categorie if ligne else Examen.Categorie.ANALYSES
        )
        examen = Examen(
            patient=bon.patient,
            categorie=categorie,
            type_examen=type_examen,
            laboratoire=bon.laboratoire_nom
            or (bon.laboratoire.nom if bon.laboratoire else ""),
            laborantin=request.user,
            medecin_prescripteur=bon.medecin,
            date=timezone.now().date(),
            statut=request.data.get("statut") or Examen.Statut.NORMAL,
            resultat_texte=request.data.get("resultat_texte") or "",
            commentaire_labo=request.data.get("commentaire_labo") or "",
            bon=bon,
            ligne=ligne,
        )
        f = request.FILES.get("fichier")
        if f:
            examen.fichier = f
        examen.save()
        if bon.statut in (BonExamen.Statut.DEMANDE, BonExamen.Statut.RECU):
            bon.statut = BonExamen.Statut.EN_COURS
            bon.save(update_fields=["statut", "updated_at"])
        bon.refresh_statut_from_lignes()
        log_action(
            request,
            "deposer_resultat_examen",
            target=type_examen,
            patient_npi=bon.patient.npi,
        )
        notify_patient_dossier_change(
            bon.patient,
            title="Résultat d'examen",
            body=f"Résultat disponible : {type_examen}.",
            notif_type=Notification.Type.EXAMEN,
            event_type="examen",
            section="examens",
            payload={
                "kind": "bon_resultat",
                "examen_id": examen.id,
                "bon_id": bon.id,
            },
            actor=request.user,
        )
        return Response(
            {
                "examen": ExamenSerializer(examen, context={"request": request}).data,
                "bon": BonExamenSerializer(bon, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ExamCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .catalog import load_exam_catalog

        return Response(load_exam_catalog())
