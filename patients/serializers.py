from rest_framework import serializers

from accounts.photo_utils import patient_photo_url

from .models import AccessBlock, Appointment, Assurance, DossierMedical, Patient


class AssuranceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assurance
        fields = [
            "id", "assureur", "num_police", "type_couverture",
            "valide_du", "valide_au", "droits_valides", "garanties",
        ]


class DossierMedicalSerializer(serializers.ModelSerializer):
    class Meta:
        model = DossierMedical
        fields = ["id", "antecedents", "allergies", "maladies_chroniques", "updated_at"]


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_npi = serializers.CharField(source="patient.npi", read_only=True)
    patient_photo_url = serializers.SerializerMethodField()
    structure_nom = serializers.CharField(source="structure.nom", read_only=True, default="")
    professionnel_nom = serializers.SerializerMethodField()
    professionnel_photo_url = serializers.SerializerMethodField()
    statut_label = serializers.CharField(source="get_statut_display", read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "patient",
            "patient_name",
            "patient_npi",
            "patient_photo_url",
            "structure",
            "structure_nom",
            "professionnel",
            "professionnel_nom",
            "professionnel_photo_url",
            "created_by",
            "debut",
            "fin",
            "motif",
            "notes",
            "statut",
            "statut_label",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "created_at", "updated_at"]

    def get_professionnel_nom(self, obj):
        if not obj.professionnel:
            return ""
        return obj.professionnel.get_full_name() or obj.professionnel.username

    def get_patient_photo_url(self, obj):
        return patient_photo_url(obj.patient, request=self.context.get("request"))

    def get_professionnel_photo_url(self, obj):
        from accounts.photo_utils import user_photo_url

        if not obj.professionnel:
            return None
        return user_photo_url(obj.professionnel, request=self.context.get("request"))


class AccessBlockSerializer(serializers.ModelSerializer):
    blocked_user_name = serializers.SerializerMethodField()
    blocked_user_role = serializers.SerializerMethodField()
    blocked_structure_nom = serializers.CharField(
        source="blocked_structure.nom", read_only=True, default=""
    )
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_npi = serializers.CharField(source="patient.npi", read_only=True)

    class Meta:
        model = AccessBlock
        fields = [
            "id",
            "patient",
            "patient_name",
            "patient_npi",
            "blocked_user",
            "blocked_user_name",
            "blocked_user_role",
            "blocked_structure",
            "blocked_structure_nom",
            "reason",
            "active",
            "created_at",
            "lifted_at",
            "created_by_admin",
        ]
        read_only_fields = ["created_at", "lifted_at", "created_by_admin"]

    def get_blocked_user_name(self, obj):
        if not obj.blocked_user:
            return ""
        return obj.blocked_user.get_full_name() or obj.blocked_user.username

    def get_blocked_user_role(self, obj):
        if not obj.blocked_user:
            return ""
        try:
            return obj.blocked_user.get_role_display()
        except Exception:
            return getattr(obj.blocked_user, "role", "")


class PatientListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id", "npi", "nom", "prenom", "full_name", "date_naissance",
            "sexe", "groupe_sanguin", "electrophorese", "photo", "photo_url",
            "npi_verifie_anip",
        ]

    def get_photo_url(self, obj):
        return patient_photo_url(obj, request=self.context.get("request"))


class UrgenceSerializer(serializers.ModelSerializer):
    """En-tête d'urgence commun à toutes les vues (CDC §3.4, §4.9)."""

    full_name = serializers.CharField(read_only=True)
    allergies = serializers.SerializerMethodField()
    maladies_chroniques = serializers.SerializerMethodField()
    assureur = serializers.SerializerMethodField()
    num_police = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id", "npi", "full_name", "groupe_sanguin", "electrophorese",
            "photo_url", "allergies", "maladies_chroniques",
            "contact_urgence_nom", "contact_urgence_lien", "tel_urgence",
            "assureur", "num_police",
        ]

    def get_photo_url(self, obj):
        return patient_photo_url(obj, request=self.context.get("request"))

    def get_allergies(self, obj):
        return getattr(getattr(obj, "dossier", None), "allergies", []) or []

    def get_maladies_chroniques(self, obj):
        return getattr(getattr(obj, "dossier", None), "maladies_chroniques", []) or []

    def get_assureur(self, obj):
        assurance = getattr(obj, "assurance", None)
        if not assurance or not getattr(assurance, "droits_valides", True):
            return ""
        return assurance.assureur or ""

    def get_num_police(self, obj):
        assurance = getattr(obj, "assurance", None)
        if not assurance or not getattr(assurance, "droits_valides", True):
            return ""
        return assurance.num_police or ""


class PatientDetailSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    dossier = DossierMedicalSerializer(read_only=True)
    assurance = AssuranceSerializer(read_only=True)
    urgence = serializers.SerializerMethodField()
    has_pin = serializers.BooleanField(read_only=True)
    require_unlock = serializers.BooleanField(read_only=True)
    urgence_when_locked = serializers.BooleanField(read_only=True)
    photo_url = serializers.SerializerMethodField()
    photo_required = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id", "npi", "npi_verifie_anip", "nom", "prenom", "full_name",
            "date_naissance", "lieu_naissance", "sexe",
            "nom_pere", "nom_mere", "adresse_commune", "adresse_quartier",
            "groupe_sanguin", "electrophorese",
            "photo", "photo_url", "photo_required",
            "telephone", "email", "has_pin", "require_unlock", "urgence_when_locked",
            "contact_urgence_nom", "contact_urgence_lien", "tel_urgence",
            "dossier", "assurance", "urgence", "created_at",
        ]

    def get_urgence(self, obj):
        return UrgenceSerializer(obj, context=self.context).data

    def get_photo_url(self, obj):
        return patient_photo_url(obj, request=self.context.get("request"))

    def get_photo_required(self, obj):
        if getattr(obj, "photo", None):
            return False
        user = getattr(obj, "user", None)
        if user is not None and getattr(user, "photo", None):
            return False
        return True


class PatientWriteSerializer(serializers.ModelSerializer):
    allergies = serializers.ListField(
        child=serializers.CharField(), required=False, write_only=True
    )
    maladies_chroniques = serializers.ListField(required=False, write_only=True)

    class Meta:
        model = Patient
        fields = [
            "id", "npi", "nom", "prenom", "date_naissance", "lieu_naissance", "sexe",
            "nom_pere", "nom_mere", "adresse_commune", "adresse_quartier",
            "groupe_sanguin", "electrophorese", "telephone", "email", "photo",
            "contact_urgence_nom", "contact_urgence_lien", "tel_urgence",
            "allergies", "maladies_chroniques",
        ]

    def _sync_dossier(self, patient, allergies, maladies):
        dossier, _ = DossierMedical.objects.get_or_create(patient=patient)
        if allergies is not None:
            dossier.allergies = allergies
        if maladies is not None:
            dossier.maladies_chroniques = maladies
        dossier.save()

    def create(self, validated_data):
        allergies = validated_data.pop("allergies", None)
        maladies = validated_data.pop("maladies_chroniques", None)
        patient = super().create(validated_data)
        self._sync_dossier(patient, allergies or [], maladies or [])
        return patient

    def update(self, instance, validated_data):
        allergies = validated_data.pop("allergies", None)
        maladies = validated_data.pop("maladies_chroniques", None)
        patient = super().update(instance, validated_data)
        if allergies is not None or maladies is not None:
            self._sync_dossier(patient, allergies, maladies)
        return patient
