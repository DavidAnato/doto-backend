"""KYC patient / professionnel + affiliations (validation admin)."""
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.utils import log_action
from core.permissions import IsAdmin, Roles

from .models import AffiliationPro, KycDossier, StructureSante, User
from .serializers import AffiliationProSerializer, KycDossierSerializer, KycPatchSerializer


def _kyc_for(user) -> KycDossier:
    subject = (
        KycDossier.Subject.PATIENT
        if getattr(user, "role", None) == Roles.PATIENT
        else KycDossier.Subject.PROFESSIONNEL
    )
    obj, _created = KycDossier.objects.get_or_create(user=user, defaults={"subject": subject})
    if obj.subject != subject:
        obj.subject = subject
        obj.save(update_fields=["subject"])
    return obj


def _file_abs(request, field):
    if not field:
        return None
    url = field.url
    if request:
        return request.build_absolute_uri(url)
    return url


class MyKycView(APIView):
    """GET/PATCH dossier KYC du compte courant."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        kyc = _kyc_for(request.user)
        return Response(KycDossierSerializer(kyc, context={"request": request}).data)

    def patch(self, request):
        kyc = _kyc_for(request.user)
        if kyc.statut == KycDossier.Statut.VALIDE:
            return Response(
                {"detail": "Dossier déjà validé."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ser = KycPatchSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        for field in (
            "nom", "prenom", "date_naissance", "lieu_naissance",
            "npi", "telephone", "sexe", "ocr_payload",
        ):
            if field in data:
                setattr(kyc, field, data[field])
        if kyc.statut == KycDossier.Statut.REFUSE:
            kyc.statut = KycDossier.Statut.BROUILLON
            kyc.motif_refus = ""
        kyc.save()
        return Response(KycDossierSerializer(kyc, context={"request": request}).data)


class MyKycSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        kyc = _kyc_for(request.user)
        if kyc.statut == KycDossier.Statut.VALIDE:
            return Response(
                {"detail": "Dossier déjà validé."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        missing = []
        if not kyc.piece_recto:
            missing.append("pièce d'identité recto")
        if not kyc.piece_verso:
            missing.append("pièce d'identité verso")
        if not kyc.selfie:
            missing.append("selfie / preuve")
        if not (kyc.nom or request.user.last_name):
            missing.append("nom")
        if not (kyc.prenom or request.user.first_name):
            missing.append("prénom")
        if missing:
            return Response(
                {"detail": "Complétez : " + ", ".join(missing) + "."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kyc.statut = KycDossier.Statut.EN_ATTENTE
        kyc.submitted_at = timezone.now()
        kyc.motif_refus = ""
        kyc.save(update_fields=["statut", "submitted_at", "motif_refus", "updated_at"])
        log_action(request, "kyc_submit", target=request.user.username)
        return Response(KycDossierSerializer(kyc, context={"request": request}).data)


class MyKycUploadView(APIView):
    """Upload pièce recto / verso / selfie."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, kind):
        if kind not in ("recto", "verso", "selfie"):
            return Response({"detail": "Type de pièce invalide."}, status=status.HTTP_400_BAD_REQUEST)
        uploaded = request.FILES.get("file") or request.FILES.get("photo") or request.FILES.get("image")
        if not uploaded:
            return Response(
                {"detail": "Fichier « file » requis (multipart)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kyc = _kyc_for(request.user)
        if kyc.statut == KycDossier.Statut.VALIDE:
            return Response(
                {"detail": "Dossier déjà validé."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        field = {"recto": "piece_recto", "verso": "piece_verso", "selfie": "selfie"}[kind]
        current = getattr(kyc, field)
        if current:
            current.delete(save=False)
        setattr(kyc, field, uploaded)
        if kyc.statut == KycDossier.Statut.REFUSE:
            kyc.statut = KycDossier.Statut.BROUILLON
            kyc.motif_refus = ""
        kyc.save()
        log_action(request, f"kyc_upload_{kind}", target=request.user.username)
        return Response(KycDossierSerializer(kyc, context={"request": request}).data)


class AdminKycViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = KycDossier.objects.select_related("user", "reviewed_by").all()
    serializer_class = KycDossierSerializer
    permission_classes = [IsAdmin]
    filterset_fields = ["statut", "subject"]
    search_fields = ["nom", "prenom", "npi", "user__username"]

    def get_queryset(self):
        qs = super().get_queryset()
        statut = self.request.query_params.get("statut")
        subject = self.request.query_params.get("subject")
        if statut:
            qs = qs.filter(statut=statut)
        if subject:
            qs = qs.filter(subject=subject)
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        kyc = self.get_object()
        kyc.statut = KycDossier.Statut.VALIDE
        kyc.motif_refus = ""
        kyc.reviewed_at = timezone.now()
        kyc.reviewed_by = request.user
        kyc.save(update_fields=["statut", "motif_refus", "reviewed_at", "reviewed_by", "updated_at"])
        log_action(request, "kyc_approve", target=kyc.user.username)
        return Response(KycDossierSerializer(kyc, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        motif = (request.data.get("motif") or request.data.get("motif_refus") or "").strip()
        if not motif:
            return Response(
                {"detail": "Indiquez le motif de refus."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kyc = self.get_object()
        kyc.statut = KycDossier.Statut.REFUSE
        kyc.motif_refus = motif
        kyc.reviewed_at = timezone.now()
        kyc.reviewed_by = request.user
        kyc.save(update_fields=["statut", "motif_refus", "reviewed_at", "reviewed_by", "updated_at"])
        log_action(request, "kyc_reject", target=kyc.user.username)
        return Response(KycDossierSerializer(kyc, context={"request": request}).data)


class MyAffiliationViewSet(viewsets.ModelViewSet):
    """Liste / création des rattachements du professionnel connecté."""

    serializer_class = AffiliationProSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return AffiliationPro.objects.filter(user=self.request.user).select_related("structure")

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == Roles.PATIENT:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Réservé aux professionnels.")
        affiliation = serializer.save(user=user, statut=AffiliationPro.Statut.EN_ATTENTE)
        if affiliation.principal:
            AffiliationPro.objects.filter(user=user).exclude(pk=affiliation.pk).update(principal=False)
        self._sync_user_structures(user)

    def perform_update(self, serializer):
        affiliation = serializer.save()
        if affiliation.statut == AffiliationPro.Statut.VALIDE:
            affiliation.statut = AffiliationPro.Statut.EN_ATTENTE
            affiliation.save(update_fields=["statut", "updated_at"])
        if affiliation.principal:
            AffiliationPro.objects.filter(user=affiliation.user).exclude(pk=affiliation.pk).update(
                principal=False
            )
        self._sync_user_structures(affiliation.user)

    def perform_destroy(self, instance):
        user = instance.user
        instance.delete()
        self._sync_user_structures(user)

    def _sync_user_structures(self, user: User):
        ids = list(
            AffiliationPro.objects.filter(user=user, structure__isnull=False)
            .exclude(statut=AffiliationPro.Statut.REFUSE)
            .values_list("structure_id", flat=True)
        )
        user.structures.set(ids)
        principal = (
            AffiliationPro.objects.filter(user=user, principal=True, structure__isnull=False)
            .exclude(statut=AffiliationPro.Statut.REFUSE)
            .first()
        )
        if principal:
            user.structure_principale = principal.structure
            user.save(update_fields=["structure_principale"])


class AdminAffiliationViewSet(viewsets.ModelViewSet):
    queryset = AffiliationPro.objects.select_related("user", "structure").all()
    serializer_class = AffiliationProSerializer
    permission_classes = [IsAdmin]
    filterset_fields = ["statut", "kind", "user", "principal"]

    def perform_create(self, serializer):
        user_id = self.request.data.get("user")
        user = User.objects.filter(pk=user_id).first() if user_id else None
        if user is None:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"user": "Utilisateur requis."})
        serializer.save(user=user, statut=AffiliationPro.Statut.VALIDE)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        aff = self.get_object()
        aff.statut = AffiliationPro.Statut.VALIDE
        aff.motif_refus = ""
        aff.save(update_fields=["statut", "motif_refus", "updated_at"])
        log_action(request, "affiliation_approve", target=f"user:{aff.user_id}")
        return Response(AffiliationProSerializer(aff).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        motif = (request.data.get("motif") or request.data.get("motif_refus") or "").strip()
        aff = self.get_object()
        aff.statut = AffiliationPro.Statut.REFUSE
        aff.motif_refus = motif
        aff.save(update_fields=["statut", "motif_refus", "updated_at"])
        log_action(request, "affiliation_reject", target=f"user:{aff.user_id}")
        return Response(AffiliationProSerializer(aff).data)


def apply_pro_profile(user, data):
    """Applique les champs d'inscription professionnelle (MeView / UserWrite)."""
    update_fields = []
    for field in (
        "type_exercice",
        "ville_exercice",
        "nom_etablissement",
        "numero_autorisation",
        "numero_ordre",
        "email_pro",
        "ligne_pro",
    ):
        if field in data:
            setattr(user, field, data[field] or "")
            update_fields.append(field)
    libre = data.get("etablissement_libre") or {}
    nom_libre = (libre.get("nom") or "").strip()
    if nom_libre:
        kind = libre.get("type") or data.get("type_exercice") or StructureSante.Type.INDEPENDANT
        struct_type = {
            "pharmacie": StructureSante.Type.PHARMACIE,
            "laboratoire": StructureSante.Type.LABORATOIRE,
            "independant": StructureSante.Type.INDEPENDANT,
        }.get(kind, StructureSante.Type.CLINIQUE)
        struct, _ = StructureSante.objects.get_or_create(
            nom=nom_libre,
            localisation=(libre.get("ville") or data.get("ville_exercice") or "")[:200],
            defaults={
                "type": struct_type,
                "code_structure": f"LIB-{user.id}-{timezone.now().strftime('%H%M%S')}",
                "statut_partenaire": False,
                "commune": (libre.get("ville") or data.get("ville_exercice") or "")[:80],
            },
        )
        user.structures.add(struct)
        if not user.structure_principale_id:
            user.structure_principale = struct
            update_fields.append("structure_principale")
        user.nom_etablissement = nom_libre
        if "nom_etablissement" not in update_fields:
            update_fields.append("nom_etablissement")
        AffiliationPro.objects.update_or_create(
            user=user,
            structure=struct,
            defaults={
                "nom_etablissement": nom_libre,
                "kind": kind if kind in dict(AffiliationPro.Kind.choices) else AffiliationPro.Kind.INDEPENDANT,
                "ville": libre.get("ville") or data.get("ville_exercice") or "",
                "numero_autorisation": data.get("numero_autorisation") or "",
                "numero_ordre": data.get("numero_ordre") or "",
                "email_pro": data.get("email_pro") or "",
                "ligne_pro": data.get("ligne_pro") or "",
                "principal": not user.affiliations.filter(principal=True).exists(),
                "statut": AffiliationPro.Statut.EN_ATTENTE,
            },
        )
    if update_fields:
        user.save(update_fields=list(dict.fromkeys(update_fields)))
    return user
