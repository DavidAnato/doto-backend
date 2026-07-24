from rest_framework import serializers



from patients.serializers import PatientListSerializer



from .models import DodoCard





class DodoCardSerializer(serializers.ModelSerializer):

    patient_detail = PatientListSerializer(source="patient", read_only=True)

    statut_label = serializers.CharField(source="get_statut_display", read_only=True)

    is_active = serializers.BooleanField(read_only=True)

    groupe_sanguin = serializers.CharField(source="patient.groupe_sanguin", read_only=True)

    patient_nom = serializers.SerializerMethodField()



    class Meta:

        model = DodoCard

        fields = [

            "id",

            "patient",

            "patient_detail",

            "patient_nom",

            "token_chiffre",

            "cvv",

            "statut",

            "statut_label",

            "is_active",

            "groupe_sanguin",

            "date_creation",

            "date_expiration",

            "revoquee_le",

            "lost_at",

            "motif",

        ]

        read_only_fields = [

            "token_chiffre",

            "date_creation",

            "revoquee_le",

            "lost_at",

        ]



    def get_patient_nom(self, obj):

        return obj.patient.full_name





class ScanSerializer(serializers.Serializer):

    """Scan douchette / caméra : résolution d'un token QR (CDC §2.5, §3.3)."""



    token = serializers.CharField()

    emergency = serializers.BooleanField(required=False, default=False)

