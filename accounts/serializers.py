from rest_framework import serializers

from core.contracts import PIN_ERROR, PIN_REGEX, OTP_ERROR, OTP_REGEX, HOSPITAL_REQUIRED_ROLES
from core.permissions import Roles

from .models import AffiliationPro, KycDossier, StructureSante
from .phone import normalize_phone
from .photo_utils import user_photo_url

from django.contrib.auth import get_user_model

User = get_user_model()

ALLOWED_PRO_REGISTER_ROLES = (
    Roles.MEDECIN,
    Roles.INFIRMIER,
    Roles.PHARMACIEN,
    Roles.LABORANTIN,
    Roles.AMBULANCIER,
    Roles.RECEPTIONNISTE,
)


class StructureSanteSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_type_display", read_only=True)
    nb_professionnels = serializers.IntegerField(
        source="professionnels.count", read_only=True
    )

    class Meta:
        model = StructureSante
        fields = [
            "id", "nom", "type", "type_label", "localisation",
            "code_structure", "statut_partenaire", "telephone",
            "catalog_id", "full_name", "ownership", "department",
            "commune", "address", "latitude", "longitude",
            "nb_professionnels", "created_at",
        ]


class UserSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    structures = StructureSanteSerializer(many=True, read_only=True)
    structure_ids = serializers.PrimaryKeyRelatedField(
        many=True, write_only=True, required=False,
        queryset=StructureSante.objects.all(), source="structures",
    )
    photo_url = serializers.SerializerMethodField()
    photo_required = serializers.SerializerMethodField()
    pin_set = serializers.BooleanField(source="has_pin", read_only=True)
    affiliations = serializers.SerializerMethodField()
    kyc = serializers.SerializerMethodField()
    pending_validation = serializers.SerializerMethodField()
    compte_statut = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name", "full_name",
            "email", "telephone", "role", "role_label", "actif",
            "is_locked", "structures", "structure_ids", "structure_principale",
            "specialite",
            "type_exercice", "ville_exercice", "nom_etablissement",
            "numero_autorisation", "numero_ordre", "email_pro", "ligne_pro",
            "affiliations", "kyc", "pending_validation", "compte_statut",
            "photo_url", "photo_required", "pin_set", "date_joined",
        ]
        read_only_fields = [
            "is_locked", "date_joined", "photo_url", "photo_required", "pin_set",
            "pending_validation", "compte_statut",
        ]

    def get_photo_url(self, obj):
        request = self.context.get("request")
        return user_photo_url(obj, request=request)

    def get_photo_required(self, obj):
        request = self.context.get("request")
        return not bool(user_photo_url(obj, request=request))

    def get_affiliations(self, obj):
        qs = getattr(obj, "affiliations", None)
        if qs is None:
            return []
        return AffiliationProSerializer(qs.all(), many=True, context=self.context).data

    def get_kyc(self, obj):
        kyc = getattr(obj, "kyc", None)
        if kyc is None:
            return None
        return KycDossierSerializer(kyc, context=self.context).data

    def get_pending_validation(self, obj):
        if not getattr(obj, "actif", True):
            return False
        kyc = getattr(obj, "kyc", None)
        if kyc is not None and kyc.statut == KycDossier.Statut.EN_ATTENTE:
            return True
        affiliations = getattr(obj, "affiliations", None)
        if affiliations is None:
            return False
        cached = None
        cache = getattr(obj, "_prefetched_objects_cache", None)
        if cache is not None:
            cached = cache.get("affiliations")
        if cached is not None:
            return any(a.statut == AffiliationPro.Statut.EN_ATTENTE for a in cached)
        return affiliations.filter(statut=AffiliationPro.Statut.EN_ATTENTE).exists()

    def get_compte_statut(self, obj):
        if not getattr(obj, "actif", True):
            return "desactive"
        if self.get_pending_validation(obj):
            return "en_attente"
        return "actif"


class UserWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    structure_ids = serializers.PrimaryKeyRelatedField(
        many=True, required=False,
        queryset=StructureSante.objects.all(), source="structures",
    )

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name", "email",
            "telephone", "role", "actif", "password",
            "structure_ids", "structure_principale", "specialite", "photo",
            "type_exercice", "ville_exercice", "nom_etablissement",
            "numero_autorisation", "numero_ordre", "email_pro", "ligne_pro",
        ]

    def validate(self, attrs):
        role = attrs.get("role") or getattr(self.instance, "role", None)
        structures = attrs.get("structures")
        principale = attrs.get("structure_principale")
        creating = self.instance is None
        needs = role in HOSPITAL_REQUIRED_ROLES
        type_ex = attrs.get("type_exercice") or getattr(self.instance, "type_exercice", "")
        nom_etab = (attrs.get("nom_etablissement") or getattr(self.instance, "nom_etablissement", "") or "").strip()
        independant = type_ex == "independant" and bool(nom_etab)
        if creating and needs and not independant:
            if not structures:
                raise serializers.ValidationError(
                    {"structure_ids": "Choisissez au moins un établissement, ou indiquez un établissement libre (indépendant)."}
                )
            if not principale:
                raise serializers.ValidationError(
                    {"structure_principale": "Désignez l'établissement principal."}
                )
        if principale and structures is not None:
            ids = [s.pk for s in structures]
            if ids and principale.pk not in ids:
                raise serializers.ValidationError(
                    {"structure_principale": "Le principal doit faire partie des hôpitaux choisis."}
                )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None) or User.objects.make_random_password()
        structures = validated_data.pop("structures", [])
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        if structures:
            user.structures.set(structures)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        structures = validated_data.pop("structures", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        if structures is not None:
            instance.structures.set(structures)
        return instance


class MeUpdateSerializer(serializers.Serializer):
    """Mise à jour profil self-service (pro & patient)."""

    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    telephone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    email = serializers.EmailField(required=False, allow_blank=True)
    specialite = serializers.CharField(required=False, allow_blank=True, max_length=80)
    structure_principale = serializers.PrimaryKeyRelatedField(
        required=False, allow_null=True, queryset=StructureSante.objects.all()
    )
    structure_ids = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=StructureSante.objects.all()
    )
    require_unlock = serializers.BooleanField(required=False)
    urgence_when_locked = serializers.BooleanField(required=False)
    type_exercice = serializers.CharField(required=False, allow_blank=True, max_length=32)
    ville_exercice = serializers.CharField(required=False, allow_blank=True, max_length=120)
    nom_etablissement = serializers.CharField(required=False, allow_blank=True, max_length=200)
    numero_autorisation = serializers.CharField(required=False, allow_blank=True, max_length=80)
    numero_ordre = serializers.CharField(required=False, allow_blank=True, max_length=80)
    email_pro = serializers.EmailField(required=False, allow_blank=True)
    ligne_pro = serializers.CharField(required=False, allow_blank=True, max_length=30)
    etablissement_libre = serializers.DictField(required=False)


class AffiliationProSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    statut_label = serializers.CharField(source="get_statut_display", read_only=True)
    structure_nom = serializers.CharField(source="structure.nom", read_only=True, allow_null=True)

    class Meta:
        model = AffiliationPro
        fields = [
            "id", "user", "structure", "structure_nom", "nom_etablissement",
            "kind", "kind_label", "ville", "numero_autorisation", "numero_ordre",
            "email_pro", "ligne_pro", "principal", "statut", "statut_label",
            "motif_refus", "created_at", "updated_at",
        ]
        read_only_fields = ["user", "statut", "motif_refus", "created_at", "updated_at"]


class KycDossierSerializer(serializers.ModelSerializer):
    statut_label = serializers.CharField(source="get_statut_display", read_only=True)
    piece_recto_url = serializers.SerializerMethodField()
    piece_verso_url = serializers.SerializerMethodField()
    selfie_url = serializers.SerializerMethodField()
    user_username = serializers.CharField(source="user.username", read_only=True)
    user_role = serializers.CharField(source="user.role", read_only=True)

    class Meta:
        model = KycDossier
        fields = [
            "id", "user", "user_username", "user_role", "subject",
            "statut", "statut_label", "motif_refus",
            "piece_recto", "piece_verso", "selfie",
            "piece_recto_url", "piece_verso_url", "selfie_url",
            "nom", "prenom", "date_naissance", "lieu_naissance",
            "npi", "telephone", "sexe", "ocr_payload",
            "submitted_at", "reviewed_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "user", "statut", "motif_refus", "piece_recto", "piece_verso", "selfie",
            "submitted_at", "reviewed_at", "created_at", "updated_at",
        ]

    def _abs(self, f):
        if not f:
            return None
        request = self.context.get("request")
        url = f.url
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_piece_recto_url(self, obj):
        return self._abs(obj.piece_recto)

    def get_piece_verso_url(self, obj):
        return self._abs(obj.piece_verso)

    def get_selfie_url(self, obj):
        return self._abs(obj.selfie)


class KycPatchSerializer(serializers.Serializer):
    nom = serializers.CharField(required=False, allow_blank=True, max_length=120)
    prenom = serializers.CharField(required=False, allow_blank=True, max_length=120)
    date_naissance = serializers.DateField(required=False, allow_null=True)
    lieu_naissance = serializers.CharField(required=False, allow_blank=True, max_length=120)
    npi = serializers.CharField(required=False, allow_blank=True, max_length=30)
    telephone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    sexe = serializers.CharField(required=False, allow_blank=True, max_length=1)
    ocr_payload = serializers.DictField(required=False)


class ProLoginSerializer(serializers.Serializer):
    """Connexion professionnelle : identifiant + mot de passe (JWT). OTP non requis."""

    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})


class ProRegisterSerializer(serializers.Serializer):
    """Inscription publique d'un professionnel (pas admin, pas patient)."""

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(
        min_length=8, write_only=True, style={"input_type": "password"}
    )
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=ALLOWED_PRO_REGISTER_ROLES)
    type_exercice = serializers.ChoiceField(choices=User.TypeExercice.choices)
    ville_exercice = serializers.CharField(
        required=False, allow_blank=True, max_length=120, default=""
    )
    nom_etablissement = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )
    numero_autorisation = serializers.CharField(
        required=False, allow_blank=True, max_length=80, default=""
    )
    numero_ordre = serializers.CharField(
        required=False, allow_blank=True, max_length=80, default=""
    )
    email_pro = serializers.EmailField(required=False, allow_blank=True, default="")
    ligne_pro = serializers.CharField(
        required=False, allow_blank=True, max_length=30, default=""
    )
    specialite = serializers.CharField(
        required=False, allow_blank=True, max_length=80, default=""
    )
    structure_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    structure_principale = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )
    etablissement_libre = serializers.DictField(required=False)

    def validate_username(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Identifiant requis.")
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Cet identifiant est déjà utilisé.")
        return value

    def validate_role(self, value):
        if value in (Roles.ADMIN, Roles.PATIENT):
            raise serializers.ValidationError("Rôle non autorisé pour l'inscription publique.")
        if value not in ALLOWED_PRO_REGISTER_ROLES:
            raise serializers.ValidationError("Rôle professionnel invalide.")
        return value

    def validate(self, attrs):
        email = (attrs.get("email") or attrs.get("email_pro") or "").strip()
        if email and User.objects.filter(email__iexact=email).exclude(email="").exists():
            raise serializers.ValidationError({"email": "Cet email est déjà utilisé."})
        attrs["email"] = email
        kind = attrs.get("type_exercice")
        nom = (attrs.get("nom_etablissement") or "").strip()
        libre = attrs.get("etablissement_libre") or {}
        nom_libre = (libre.get("nom") or nom).strip()
        ids = attrs.get("structure_ids") or []
        principale = attrs.get("structure_principale")
        independant = kind == User.TypeExercice.INDEPENDANT
        if independant:
            if not nom_libre:
                raise serializers.ValidationError(
                    {"nom_etablissement": "Indiquez le nom de votre cabinet / exercice."}
                )
        else:
            if not ids and not nom_libre:
                raise serializers.ValidationError(
                    {"structure_ids": "Choisissez un établissement ou saisissez un nom."}
                )
            if ids and not principale:
                raise serializers.ValidationError(
                    {"structure_principale": "Désignez l'établissement principal."}
                )
            if principale and ids and principale not in ids:
                raise serializers.ValidationError(
                    {
                        "structure_principale": (
                            "Le principal doit faire partie des établissements choisis."
                        )
                    }
                )
        if ids:
            found = set(
                StructureSante.objects.filter(pk__in=ids).values_list("id", flat=True)
            )
            if any(i not in found for i in ids):
                raise serializers.ValidationError(
                    {"structure_ids": "Établissement introuvable."}
                )
        if nom_libre and not (libre.get("nom") or "").strip():
            attrs["etablissement_libre"] = {
                "nom": nom_libre,
                "ville": attrs.get("ville_exercice") or "",
                "type": kind,
            }
        ligne = attrs.get("ligne_pro") or ""
        if ligne:
            attrs["ligne_pro"] = normalize_phone(ligne)
        return attrs


class RequestOtpSerializer(serializers.Serializer):
    """OTP : login / inscription patient (pas pour login pro)."""

    PURPOSE_CHOICES = ("login", "register", "password_change", "password_reset")

    phone = serializers.CharField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    purpose = serializers.ChoiceField(choices=PURPOSE_CHOICES, default="login")

    def validate(self, attrs):
        phone = (attrs.get("phone") or "").strip()
        if not phone:
            raise serializers.ValidationError({"phone": "Numéro de téléphone requis."})
        attrs["phone"] = phone
        return attrs


class PatientLoginSerializer(serializers.Serializer):
    """Connexion patient : téléphone + OTP uniquement (pas de mot de passe)."""

    phone = serializers.CharField()
    otp = serializers.RegexField(
        regex=OTP_REGEX,
        error_messages={"invalid": OTP_ERROR},
    )


class PatientRegisterSerializer(serializers.Serializer):
    """Inscription patient - téléphone + OTP (+ identité OCR). Pas de mot de passe."""

    phone = serializers.CharField()
    otp = serializers.RegexField(
        regex=OTP_REGEX,
        error_messages={"invalid": OTP_ERROR},
    )
    first_name = serializers.CharField(required=False, allow_blank=True, default="")
    last_name = serializers.CharField(required=False, allow_blank=True, default="")
    npi = serializers.CharField(required=False, allow_blank=True, default="")
    birth_date = serializers.CharField(required=False, allow_blank=True, default="")
    birth_place = serializers.CharField(required=False, allow_blank=True, default="")
    father_name = serializers.CharField(required=False, allow_blank=True, default="")
    mother_name = serializers.CharField(required=False, allow_blank=True, default="")
    address_commune = serializers.CharField(required=False, allow_blank=True, default="")
    address_quartier = serializers.CharField(required=False, allow_blank=True, default="")
    password = serializers.CharField(
        required=False, allow_blank=True, style={"input_type": "password"}
    )


class PatientPasswordChangeSerializer(serializers.Serializer):
    """Legacy - changement MDP patient via OTP (déprécié, comptes sans MDP)."""

    phone = serializers.CharField()
    otp = serializers.RegexField(
        regex=OTP_REGEX,
        error_messages={"invalid": OTP_ERROR},
    )
    new_password = serializers.CharField(min_length=6, style={"input_type": "password"})


class PatientPinLoginSerializer(serializers.Serializer):
    """Déverrouillage secondaire NPI + PIN 4 chiffres (hors connexion principale)."""

    npi = serializers.CharField()
    pin = serializers.RegexField(
        regex=PIN_REGEX,
        error_messages={"invalid": PIN_ERROR},
    )


class SetPinSerializer(serializers.Serializer):
    pin = serializers.RegexField(
        regex=PIN_REGEX,
        error_messages={"invalid": PIN_ERROR},
    )
    old_pin = serializers.CharField(required=False, allow_blank=True)


class VerifyPinSerializer(serializers.Serializer):
    pin = serializers.RegexField(
        regex=PIN_REGEX,
        error_messages={"invalid": PIN_ERROR},
    )
