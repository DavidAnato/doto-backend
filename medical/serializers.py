from rest_framework import serializers

from .models import Consultation, ConstanteVitale, Examen, Medicament, Ordonnance


class ConsultationSerializer(serializers.ModelSerializer):
    structure_nom = serializers.CharField(source="structure.nom", read_only=True, allow_null=True)
    structure_telephone = serializers.CharField(
        source="structure.telephone", read_only=True, allow_blank=True, allow_null=True, default=""
    )
    medecin_nom = serializers.CharField(source="medecin.get_full_name", read_only=True, allow_null=True)
    medecin_telephone = serializers.CharField(
        source="medecin.telephone", read_only=True, allow_blank=True, allow_null=True, default=""
    )
    type_label = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = Consultation
        fields = [
            "id", "patient", "structure", "structure_nom", "structure_telephone",
            "medecin", "medecin_nom", "medecin_telephone",
            "date", "type", "type_label", "diagnostic", "notes", "annule", "created_at",
        ]
        read_only_fields = ["medecin", "annule"]


class MedicamentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medicament
        fields = ["id", "nom", "dosage", "frequence", "duree_jours"]


class OrdonnanceSerializer(serializers.ModelSerializer):
    medicaments = MedicamentSerializer(many=True)
    medecin_nom = serializers.CharField(source="medecin.get_full_name", read_only=True, allow_null=True)
    medecin_telephone = serializers.CharField(
        source="medecin.telephone", read_only=True, allow_blank=True, allow_null=True, default=""
    )
    patient_nom = serializers.CharField(source="patient.full_name", read_only=True)
    patient_npi = serializers.CharField(source="patient.npi", read_only=True)
    structure_nom = serializers.CharField(source="structure.nom", read_only=True, allow_null=True)
    statut_label = serializers.CharField(source="get_statut_display", read_only=True)

    class Meta:
        model = Ordonnance
        fields = [
            "id", "patient", "patient_nom", "patient_npi", "medecin", "medecin_nom",
            "medecin_telephone", "structure", "structure_nom", "consultation",
            "date", "statut", "statut_label", "instructions", "signature_electronique",
            "alertes_interactions", "dispensee_le", "medicaments", "created_at",
        ]
        read_only_fields = ["medecin", "structure", "alertes_interactions", "dispensee_le"]

    def create(self, validated_data):
        medicaments = validated_data.pop("medicaments", [])
        ordonnance = Ordonnance.objects.create(**validated_data)
        for med in medicaments:
            Medicament.objects.create(ordonnance=ordonnance, **med)
        ordonnance.alertes_interactions = detect_interactions(
            [m["nom"] for m in medicaments]
        )
        ordonnance.save(update_fields=["alertes_interactions"])
        return ordonnance

    def update(self, instance, validated_data):
        medicaments = validated_data.pop("medicaments", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if medicaments is not None:
            instance.medicaments.all().delete()
            for med in medicaments:
                Medicament.objects.create(ordonnance=instance, **med)
            instance.alertes_interactions = detect_interactions(
                [m["nom"] for m in medicaments]
            )
            instance.save(update_fields=["alertes_interactions"])
        return instance


# Base minimale d'interactions médicamenteuses (démo — CDC §3.5).
KNOWN_INTERACTIONS = [
    ({"aspirine", "warfarine"}, "Risque hémorragique majeur (Aspirine + Warfarine)."),
    ({"metformine", "alcool"}, "Risque d'acidose lactique (Metformine + Alcool)."),
    ({"amlodipine", "simvastatine"}, "Adapter la dose de simvastatine avec l'amlodipine."),
    ({"paracetamol", "warfarine"}, "Surveillance INR (Paracétamol + Warfarine)."),
]


def detect_interactions(noms):
    lowered = {n.split()[0].lower() for n in noms if n}
    alerts = []
    for combo, message in KNOWN_INTERACTIONS:
        if combo.issubset(lowered):
            alerts.append(message)
    return alerts


class ExamenSerializer(serializers.ModelSerializer):
    laborantin_nom = serializers.CharField(source="laborantin.get_full_name", read_only=True)
    patient_nom = serializers.CharField(source="patient.full_name", read_only=True)
    patient_npi = serializers.CharField(source="patient.npi", read_only=True)
    statut_label = serializers.CharField(source="get_statut_display", read_only=True)
    categorie_label = serializers.CharField(source="get_categorie_display", read_only=True)
    fichier_url = serializers.SerializerMethodField()

    class Meta:
        model = Examen
        fields = [
            "id", "patient", "patient_nom", "patient_npi",
            "categorie", "categorie_label", "type_examen",
            "laboratoire", "laborantin", "laborantin_nom", "medecin_prescripteur",
            "date", "statut", "statut_label", "resultat_texte", "commentaire_labo",
            "fichier", "fichier_url", "annule", "created_at",
        ]
        read_only_fields = ["laborantin", "annule"]

    def get_fichier_url(self, obj):
        if not obj.fichier:
            return None
        request = self.context.get("request")
        url = obj.fichier.url
        if request:
            return request.build_absolute_uri(url)
        return url


class ConstanteVitaleSerializer(serializers.ModelSerializer):
    infirmier_nom = serializers.CharField(source="infirmier.get_full_name", read_only=True)

    class Meta:
        model = ConstanteVitale
        fields = [
            "id", "patient", "infirmier", "infirmier_nom",
            "tension_systolique", "tension_diastolique",
            "temperature", "poids", "glycemie", "date",
        ]
        read_only_fields = ["infirmier", "date"]
