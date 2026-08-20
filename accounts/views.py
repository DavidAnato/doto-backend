import logging

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from audit.utils import log_action
from core.permissions import IsAdmin, Roles
from core.providers import issue_otp, verify_otp

from .models import StructureSante
from .phone import normalize_phone
from .photo_utils import validate_identity_photo
from .serializers import (
    MeUpdateSerializer,
    PatientLoginSerializer,
    PatientPasswordChangeSerializer,
    PatientPinLoginSerializer,
    PatientRegisterSerializer,
    ProLoginSerializer,
    ProRegisterSerializer,
    RequestOtpSerializer,
    SetPinSerializer,
    StructureSanteSerializer,
    UserSerializer,
    UserWriteSerializer,
    VerifyPinSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def find_patient_user(phone: str):
    """Recherche patient par téléphone (exact ou digits uniquement)."""
    candidates = {phone, normalize_phone(phone)}
    qs = User.objects.filter(role=Roles.PATIENT)
    for cand in candidates:
        user = qs.filter(telephone=cand).first() or qs.filter(username=cand).first()
        if user:
            return user
    phone_digits = "".join(ch for ch in phone if ch.isdigit())
    for user in qs.exclude(telephone="").iterator():
        stored = "".join(ch for ch in (user.telephone or user.username or "") if ch.isdigit())
        if stored and stored == phone_digits:
            return user
    return None


def user_data(user, request=None):
    return UserSerializer(user, context={"request": request}).data


def patient_extra(patient):
    return {
        "has_pin": patient.has_pin,
        "require_unlock": patient.require_unlock,
        "urgence_when_locked": patient.urgence_when_locked,
    }


def patient_payload(user, request=None):
    payload = {"user": user_data(user, request), **tokens_for(user)}
    patient = getattr(user, "patient", None)
    if patient is not None:
        from patients.serializers import PatientDetailSerializer

        data = PatientDetailSerializer(patient, context={"request": request}).data
        data.update(patient_extra(patient))
        payload["patient"] = data
    return payload


def _pin_target(user):
    """Retourne (owner, kind) - Patient pour patients, User pour pros."""
    patient = getattr(user, "patient", None)
    if user.role == Roles.PATIENT and patient is not None:
        return patient, "patient"
    return user, "pro"


def _register_failed_pin(owner, kind: str):
    owner.failed_pin_attempts += 1
    if owner.failed_pin_attempts >= settings.PATIENT_PIN_MAX_ATTEMPTS:
        owner.pin_locked_until = timezone.now() + timezone.timedelta(
            minutes=settings.LOGIN_LOCKOUT_MINUTES
        )
        owner.failed_pin_attempts = 0
    fields = ["failed_pin_attempts", "pin_locked_until"]
    owner.save(update_fields=fields)


def _clear_pin_failures(owner):
    owner.failed_pin_attempts = 0
    owner.pin_locked_until = None
    owner.save(update_fields=["failed_pin_attempts", "pin_locked_until"])


class RequestOtpView(APIView):
    """OTP SMS : login / inscription patient (pas pour login pro)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RequestOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = normalize_phone(serializer.validated_data["phone"])
        purpose = serializer.validated_data["purpose"]

        if purpose in ("login", "password_change", "password_reset"):
            exists = bool(find_patient_user(phone))
            if not exists:
                # Message générique - pas de fuite d'existence
                return Response(
                    {"detail": "Si le compte existe, un OTP a été envoyé.", "sent": True, "purpose": purpose}
                )

        if purpose == "register":
            if find_patient_user(phone) is not None:
                return Response(
                    {"detail": "Ce numéro est déjà associé à un compte."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        issue_otp(phone)
        resp = {
            "detail": "OTP envoyé.",
            "sent": True,
            "purpose": purpose,
            "provider": settings.SMS_PROVIDER,
        }
        resp["hint"] = f"Code démo {settings.DEMO_OTP_CODE} toujours accepté"
        return Response(resp)


class ProLoginView(APIView):
    """Connexion pro / admin : identifiant + mot de passe → JWT (sans OTP)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ProLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        generic_error = {"detail": "Identifiants invalides."}

        try:
            user = User.objects.get(username=data["username"])
        except User.DoesNotExist:
            return Response(generic_error, status=status.HTTP_401_UNAUTHORIZED)

        if user.is_locked:
            return Response(
                {"detail": "Compte temporairement bloqué. Réessayez plus tard."},
                status=status.HTTP_423_LOCKED,
            )

        auth_user = authenticate(username=data["username"], password=data["password"])
        if auth_user is None or auth_user.role == Roles.PATIENT:
            user.register_failed_login(
                settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_LOCKOUT_MINUTES
            )
            return Response(generic_error, status=status.HTTP_401_UNAUTHORIZED)

        if not auth_user.actif:
            return Response(
                {"detail": "Compte désactivé."}, status=status.HTTP_403_FORBIDDEN
            )

        auth_user.reset_login_state()
        log_action(request, "login", target=f"pro:{auth_user.username}")
        return Response(
            {
                "user": user_data(auth_user, request),
                "pin_set": auth_user.has_pin,
                "pin_required": True,
                **tokens_for(auth_user),
            }
        )


class ProRegisterView(APIView):
    """Inscription professionnelle publique : crée user + profil + affiliations en attente."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ProRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from .kyc_views import attach_register_affiliations
        from .models import KycDossier

        with transaction.atomic():
            user = User(
                username=data["username"],
                first_name=(data.get("first_name") or "").strip(),
                last_name=(data.get("last_name") or "").strip(),
                email=data.get("email") or data.get("email_pro") or "",
                telephone=data.get("ligne_pro") or "",
                role=data["role"],
                actif=True,
                is_staff=False,
                is_superuser=False,
                specialite=data.get("specialite") or "Médecine générale",
                type_exercice=data["type_exercice"],
                ville_exercice=data.get("ville_exercice") or "",
                nom_etablissement=data.get("nom_etablissement") or "",
                numero_autorisation=data.get("numero_autorisation") or "",
                numero_ordre=data.get("numero_ordre") or "",
                email_pro=data.get("email_pro") or data.get("email") or "",
                ligne_pro=data.get("ligne_pro") or "",
            )
            user.set_password(data["password"])
            user.save()
            attach_register_affiliations(user, data)
            KycDossier.objects.get_or_create(
                user=user,
                defaults={
                    "subject": KycDossier.Subject.PROFESSIONNEL,
                    "statut": KycDossier.Statut.EN_ATTENTE,
                    "nom": user.last_name,
                    "prenom": user.first_name,
                    "telephone": user.telephone,
                },
            )

        user = (
            User.objects.prefetch_related("structures", "affiliations")
            .select_related("structure_principale", "kyc")
            .get(pk=user.pk)
        )
        log_action(request, "register", target=f"pro:{user.username}")
        payload_user = user_data(user, request)
        return Response(
            {
                "user": payload_user,
                "pin_set": False,
                "pin_required": True,
                "pending_validation": True,
                "compte_statut": payload_user.get("compte_statut") or "en_attente",
                **tokens_for(user),
            },
            status=status.HTTP_201_CREATED,
        )


class PatientLoginView(APIView):
    """Connexion patient : téléphone + OTP uniquement (pas de mot de passe / PIN)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PatientLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        phone = normalize_phone(data["phone"])

        if not verify_otp(phone, data["otp"]):
            return Response(
                {"detail": "Code OTP invalide ou expiré.", "otp_required": True},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user = find_patient_user(phone)
        if user is None:
            return Response(
                {"detail": "Identifiants invalides."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if user.is_locked:
            return Response(
                {"detail": "Compte bloqué après trop de tentatives."},
                status=status.HTTP_423_LOCKED,
            )

        if not user.actif:
            return Response(
                {"detail": "Compte désactivé."}, status=status.HTTP_403_FORBIDDEN
            )

        user.reset_login_state()
        log_action(request, "login", target=f"patient:{user.username}")
        return Response(patient_payload(user, request))


class PatientRegisterView(APIView):
    """Inscription patient - téléphone + OTP puis profil (pas de mot de passe)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PatientRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        phone = normalize_phone(data["phone"])

        if not verify_otp(phone, data["otp"]):
            return Response(
                {"detail": "Code OTP invalide ou expiré.", "otp_required": True},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if find_patient_user(phone) is not None:
            return Response(
                {"detail": "Ce numéro est déjà associé à un compte."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from patients.models import Patient

        npi = (data.get("npi") or "").strip()
        if npi and Patient.objects.filter(npi__iexact=npi).exists():
            return Response(
                {"detail": "Ce NPI est déjà enregistré."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User(
            username=phone,
            telephone=phone,
            first_name=data.get("first_name") or "",
            last_name=data.get("last_name") or "",
            role=Roles.PATIENT,
            actif=True,
        )
        user.set_unusable_password()
        user.save()

        if not npi:
            npi = f"{user.id:010d}"

        birth = None
        raw_birth = (data.get("birth_date") or "").strip()
        if raw_birth:
            from datetime import date as date_cls

            try:
                birth = date_cls.fromisoformat(raw_birth[:10])
            except ValueError:
                birth = None

        Patient.objects.create(
            user=user,
            npi=npi,
            nom=user.last_name or "Patient",
            prenom=user.first_name or "Nouveau",
            telephone=phone,
            date_naissance=birth,
            lieu_naissance=(data.get("birth_place") or "")[:120],
            nom_pere=(data.get("father_name") or "")[:120],
            nom_mere=(data.get("mother_name") or "")[:120],
            adresse_commune=(data.get("address_commune") or "")[:120],
            adresse_quartier=(data.get("address_quartier") or "")[:120],
            npi_verifie_anip=bool(data.get("npi")),
        )
        log_action(request, "register", target=f"patient:{phone}")
        return Response(patient_payload(user, request), status=status.HTTP_201_CREATED)


class IdCardOcrView(APIView):
    """OCR CIP / carte CEDEAO - public (inscription)."""

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("image") or request.FILES.get("photo") or request.FILES.get("file")
        if not upload:
            return Response(
                {"ok": False, "code": "missing_image", "detail": "Image de la carte requise (champ image)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.size and upload.size > 12 * 1024 * 1024:
            return Response(
                {"ok": False, "code": "image_too_large", "detail": "Image trop lourde (max 12 Mo)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw = upload.read()
        logger.info("OCR upload size=%s name=%s", upload.size, getattr(upload, "name", ""))
        try:
            from .id_card_ocr import ocr_id_card

            data = ocr_id_card(raw)
        except TimeoutError as e:
            logger.warning("OCR timeout: %s", e)
            return Response(
                {
                    "ok": False,
                    "code": "ocr_timeout",
                    "detail": (
                        "Délai dépassé pendant la lecture OCR. "
                        "Réessayez avec une photo nette et bien cadrée."
                    ),
                },
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except ValueError as e:
            logger.info("OCR NPI introuvable: %s", e)
            return Response(
                {"ok": False, "code": "npi_not_found", "detail": str(e)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except RuntimeError as e:
            logger.warning("OCR indisponible: %s", e)
            return Response(
                {"ok": False, "code": "ocr_unavailable", "detail": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("OCR erreur inattendue")
            return Response(
                {
                    "ok": False,
                    "code": "ocr_error",
                    "detail": f"Lecture impossible : {e}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Ne pas renvoyer raw_text trop long au client
        payload = {k: v for k, v in data.items() if k != "raw_text"}
        payload["ok"] = True
        return Response(payload)



class PatientPasswordChangeView(APIView):
    """Legacy - changement MDP patient via OTP (comptes sans MDP : no-op utile)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PatientPasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        phone = normalize_phone(data["phone"])

        if not verify_otp(phone, data["otp"]):
            return Response(
                {"detail": "Code OTP invalide ou expiré.", "otp_required": True},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = find_patient_user(phone)
        if user is None:
            return Response(
                {"detail": "Identifiants invalides."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user.set_password(data["new_password"])
        user.reset_login_state()
        user.save(update_fields=["password"])
        log_action(request, "password_change", target=f"patient:{phone}")
        return Response({"detail": "Mot de passe mis à jour."})


class PatientPinLoginView(APIView):
    """Déverrouillage secondaire NPI + PIN 4 chiffres (pas la connexion principale)."""

    permission_classes = [AllowAny]

    def post(self, request):
        from patients.models import Patient

        serializer = PatientPinLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        npi = data["npi"].strip()
        pin = data["pin"].strip()

        patient = Patient.objects.filter(npi__iexact=npi).select_related("user").first()
        if patient is None or not patient.user:
            return Response(
                {"detail": "Identifiants invalides."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if patient.pin_locked_until and patient.pin_locked_until > timezone.now():
            return Response(
                {"detail": "PIN bloqué temporairement."},
                status=status.HTTP_423_LOCKED,
            )

        if not patient.has_pin or not patient.check_pin(pin):
            _register_failed_pin(patient, "patient")
            return Response(
                {"detail": "Identifiants invalides."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        _clear_pin_failures(patient)
        log_action(request, "login_pin", target=patient.npi, patient_npi=patient.npi)
        return Response(patient_payload(patient.user, request))


class SetPinView(APIView):
    """Définit / change le PIN (patient ou pro) - 4 chiffres, hashé."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SetPinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pin = serializer.validated_data["pin"]
        old_pin = serializer.validated_data.get("old_pin") or ""

        owner, kind = _pin_target(request.user)
        if kind == "patient" and owner is None:
            return Response(
                {"detail": "Aucun dossier patient lié."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if old_pin and owner.has_pin:
            if not owner.check_pin(old_pin):
                return Response(
                    {"detail": "Ancien PIN incorrect."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        owner.set_pin(pin)
        target = getattr(owner, "npi", None) or request.user.username
        log_action(
            request,
            "set_pin",
            target=str(target),
            patient_npi=getattr(owner, "npi", None),
        )
        return Response({"detail": "PIN enregistré.", "has_pin": True, "pin_set": True})


class VerifyPinView(APIView):
    """Vérifie le PIN de session (déverrouillage) - JWT déjà actif."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifyPinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pin = serializer.validated_data["pin"]

        owner, kind = _pin_target(request.user)
        if kind == "patient" and owner is None:
            return Response(
                {"detail": "Aucun dossier patient lié."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if owner.pin_locked_until and owner.pin_locked_until > timezone.now():
            return Response(
                {"detail": "PIN bloqué temporairement."},
                status=status.HTTP_423_LOCKED,
            )

        if not owner.has_pin or not owner.check_pin(pin):
            _register_failed_pin(owner, kind)
            return Response(
                {"detail": "PIN incorrect."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        _clear_pin_failures(owner)
        log_action(request, "verify_pin", target=request.user.username)
        return Response({"detail": "PIN validé.", "unlocked": True})


class MeView(APIView):
    """Profil courant - GET + PATCH (nom, téléphone, email, flags sécurité patient)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        data = user_data(request.user, request)
        patient = getattr(request.user, "patient", None)
        if patient is not None:
            from patients.serializers import PatientDetailSerializer

            pdata = PatientDetailSerializer(patient, context={"request": request}).data
            pdata.update(patient_extra(patient))
            data["patient"] = pdata
            data["pin_set"] = patient.has_pin
            data["require_unlock"] = patient.require_unlock
            data["urgence_when_locked"] = patient.urgence_when_locked
        else:
            data["pin_set"] = request.user.has_pin
            data["pin_required"] = request.user.role != Roles.PATIENT
        return Response(data)

    def patch(self, request):
        serializer = MeUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = request.user
        data = serializer.validated_data
        update_fields = []
        for field in ("first_name", "last_name", "telephone", "email", "specialite"):
            if field in data:
                value = data[field]
                if field == "telephone" and value:
                    value = normalize_phone(value)
                setattr(user, field, value)
                update_fields.append(field)
        if "structure_principale" in data:
            user.structure_principale = data["structure_principale"]
            update_fields.append("structure_principale")
        if update_fields:
            user.save(update_fields=update_fields)
        if "structure_ids" in data:
            ids = data["structure_ids"]
            user.structures.set(ids)
            if user.structure_principale_id and user.structure_principale_id not in {
                s.pk for s in ids
            }:
                if ids:
                    user.structure_principale = ids[0]
                    user.save(update_fields=["structure_principale"])
            # Sync affiliations from catalogue picks
            from .models import AffiliationPro

            kind = data.get("type_exercice") or user.type_exercice or AffiliationPro.Kind.ETABLISSEMENT
            principale_id = user.structure_principale_id
            for struct in ids:
                AffiliationPro.objects.update_or_create(
                    user=user,
                    structure=struct,
                    defaults={
                        "nom_etablissement": struct.nom,
                        "kind": kind if kind in dict(AffiliationPro.Kind.choices) else AffiliationPro.Kind.ETABLISSEMENT,
                        "ville": data.get("ville_exercice") or user.ville_exercice or struct.commune or "",
                        "numero_autorisation": data.get("numero_autorisation") or user.numero_autorisation or "",
                        "numero_ordre": data.get("numero_ordre") or user.numero_ordre or "",
                        "email_pro": data.get("email_pro") or user.email_pro or "",
                        "ligne_pro": data.get("ligne_pro") or user.ligne_pro or "",
                        "principal": struct.pk == principale_id,
                        "statut": AffiliationPro.Statut.EN_ATTENTE,
                    },
                )
        from .kyc_views import apply_pro_profile

        apply_pro_profile(user, data)

        patient = getattr(user, "patient", None)
        if patient is not None:
            synced = []
            if "first_name" in data:
                patient.prenom = data["first_name"] or patient.prenom
                synced.append("prenom")
            if "last_name" in data:
                patient.nom = data["last_name"] or patient.nom
                synced.append("nom")
            if "telephone" in data:
                patient.telephone = normalize_phone(data["telephone"]) if data["telephone"] else patient.telephone
                synced.append("telephone")
            if "email" in data:
                patient.email = data["email"] or patient.email
                synced.append("email")
            if "require_unlock" in data:
                patient.require_unlock = data["require_unlock"]
                synced.append("require_unlock")
            if "urgence_when_locked" in data:
                patient.urgence_when_locked = data["urgence_when_locked"]
                synced.append("urgence_when_locked")
            if synced:
                patient.save(update_fields=synced + ["updated_at"])

        if update_fields or (patient is not None and data):
            log_action(request, "update_profile", target=user.username)
        return self.get(request)


class MePhotoView(APIView):
    """Upload photo d'identité (multipart) - tous types de compte."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded = request.FILES.get("photo") or request.FILES.get("file")
        if not uploaded:
            return Response(
                {"detail": "Fichier « photo » requis (multipart)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_identity_photo(uploaded)
        except DjangoValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return Response({"detail": msg}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if user.photo:
            user.photo.delete(save=False)
        ext = (uploaded.name.rsplit(".", 1)[-1] if "." in uploaded.name else "jpg").lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        from django.utils import timezone as dj_tz

        stamp = int(dj_tz.now().timestamp())
        filename = f"user_{user.id}_{stamp}.{ext}"
        user.photo.save(filename, ContentFile(uploaded.read()), save=True)

        patient = getattr(user, "patient", None)
        if patient is not None:
            if patient.photo:
                patient.photo.delete(save=False)
            uploaded.seek(0)
            patient.photo.save(f"patient_{patient.id}.{ext}", ContentFile(uploaded.read()), save=True)

        log_action(request, "upload_photo", target=user.username)
        return Response(user_data(user, request))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        log_action(request, "logout", target=request.user.username)
        return Response({"detail": "Déconnecté."})


class StructureSanteViewSet(viewsets.ModelViewSet):
    queryset = StructureSante.objects.all()
    serializer_class = StructureSanteSerializer
    search_fields = ["nom", "localisation", "code_structure"]
    filterset_fields = ["type", "statut_partenaire"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdmin()]


class HospitalCatalogView(APIView):
    """Liste JSON des hôpitaux du Bénin (catalogue) + structures seedées.

    Public (AllowAny) : nécessaire à l'inscription professionnelle avant login.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        from .hospital_catalog import load_hospitals

        qs = StructureSante.objects.all()
        department = request.query_params.get("department")
        commune = request.query_params.get("commune")
        q = (request.query_params.get("q") or "").strip()
        catalog = load_hospitals()
        if department:
            catalog = [h for h in catalog if (h.get("department") or "").lower() == department.lower()]
            qs = qs.filter(department__iexact=department)
        if commune:
            catalog = [h for h in catalog if (h.get("commune") or "").lower() == commune.lower()]
            qs = qs.filter(commune__iexact=commune)
        if q:
            ql = q.lower()
            catalog = [
                h
                for h in catalog
                if ql in (h.get("name") or "").lower() or ql in (h.get("full_name") or "").lower()
            ]
            qs = qs.filter(nom__icontains=q)
        return Response(
            {
                "catalog": catalog,
                "structures": StructureSanteSerializer(qs[:200], many=True).data,
            }
        )


class ContractsView(APIView):
    """Contrats figés (PIN/OTP/spécialités/routing notifs) - public authentifié."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from core.contracts import contracts_payload

        return Response(contracts_payload())


class UserViewSet(viewsets.ModelViewSet):
    """Gestion des comptes professionnels - réservé aux admins (CDC §3.5)."""

    queryset = User.objects.all().prefetch_related("structures", "affiliations").select_related("structure_principale", "kyc")
    permission_classes = [IsAdmin]
    search_fields = ["username", "first_name", "last_name", "email"]
    filterset_fields = ["role", "actif"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserWriteSerializer
        return UserSerializer

    @action(detail=True, methods=["post"])
    def toggle_active(self, request, pk=None):
        user = self.get_object()
        user.actif = not user.actif
        user.save(update_fields=["actif"])
        log_action(request, "toggle_active", target=user.username)
        return Response(user_data(user, request))

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        user = self.get_object()
        user.reset_login_state()
        return Response(user_data(user, request))

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def photo(self, request, pk=None):
        """Admin : définit la photo d'identité d'un utilisateur."""
        user = self.get_object()
        uploaded = request.FILES.get("photo") or request.FILES.get("file")
        if not uploaded:
            return Response(
                {"detail": "Fichier « photo » requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_identity_photo(uploaded)
        except DjangoValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return Response({"detail": msg}, status=status.HTTP_400_BAD_REQUEST)
        if user.photo:
            user.photo.delete(save=False)
        ext = (uploaded.name.rsplit(".", 1)[-1] if "." in uploaded.name else "jpg").lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        user.photo.save(f"user_{user.id}.{ext}", ContentFile(uploaded.read()), save=True)
        return Response(user_data(user, request))

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx
