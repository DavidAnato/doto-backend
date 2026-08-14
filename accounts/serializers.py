from rest_framework import serializers

from core.contracts import PIN_ERROR, PIN_REGEX, OTP_ERROR, OTP_REGEX, HOSPITAL_REQUIRED_ROLES

from .models import StructureSante
from .photo_utils import user_photo_url

from django.contrib.auth import get_user_model

User = get_user_model()


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

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name", "full_name",
            "email", "telephone", "role", "role_label", "actif",
            "is_locked", "structures", "structure_ids", "structure_principale",
            "specialite",
            "photo_url", "photo_required", "pin_set", "date_joined",
        ]
        read_only_fields = ["is_locked", "date_joined", "photo_url", "photo_required", "pin_set"]

    def get_photo_url(self, obj):
        request = self.context.get("request")
        return user_photo_url(obj, request=request)

    def get_photo_required(self, obj):
        request = self.context.get("request")
        return not bool(user_photo_url(obj, request=request))


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
        ]

    def validate(self, attrs):
        role = attrs.get("role") or getattr(self.instance, "role", None)
        structures = attrs.get("structures")
        principale = attrs.get("structure_principale")
        creating = self.instance is None
        needs = role in HOSPITAL_REQUIRED_ROLES
        if creating and needs:
            if not structures:
                raise serializers.ValidationError(
                    {"structure_ids": "Choisissez au moins un hôpital."}
                )
            if not principale:
                raise serializers.ValidationError(
                    {"structure_principale": "Désignez l'hôpital principal."}
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


class ProLoginSerializer(serializers.Serializer):
    """Connexion professionnelle : identifiant + mot de passe (JWT). OTP non requis."""

    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})


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
    """Inscription patient — téléphone + OTP (+ identité OCR). Pas de mot de passe."""

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
    """Legacy — changement MDP patient via OTP (déprécié, comptes sans MDP)."""

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
