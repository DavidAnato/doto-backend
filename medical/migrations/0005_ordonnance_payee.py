from django.db import migrations, models
import django.db.models.deletion


def migrate_dispensee_to_payee(apps, schema_editor):
    Ordonnance = apps.get_model("medical", "Ordonnance")
    Ordonnance.objects.filter(statut="dispensee").update(statut="payee")


def reverse_payee_to_dispensee(apps, schema_editor):
    Ordonnance = apps.get_model("medical", "Ordonnance")
    Ordonnance.objects.filter(statut="payee").update(statut="dispensee")


class Migration(migrations.Migration):

    dependencies = [
        ("medical", "0004_consultation_bon_examen"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ordonnance",
            name="statut",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("terminee", "Terminée"),
                    ("payee", "Payé"),
                    ("dispensee", "Payé"),
                    ("annulee", "Annulée"),
                ],
                default="active",
                max_length=16,
            ),
        ),
        migrations.RunPython(migrate_dispensee_to_payee, reverse_payee_to_dispensee),
    ]
