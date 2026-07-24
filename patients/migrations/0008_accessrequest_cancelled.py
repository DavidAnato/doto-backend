# Generated manually for AccessRequest.Status.CANCELLED

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0007_patient_filiation_adresse"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accessrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "En attente"),
                    ("approved", "Approuvé"),
                    ("denied", "Refusé"),
                    ("expired", "Expiré"),
                    ("emergency_bypass", "Urgence (bypass)"),
                    ("revoked", "Révoqué"),
                    ("cancelled", "Annulé (pro)"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
    ]
