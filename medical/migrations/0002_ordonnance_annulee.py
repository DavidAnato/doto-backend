# Generated manually for Ordonnance.Statut.ANNULEE

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("medical", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ordonnance",
            name="statut",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("terminee", "Terminée"),
                    ("dispensee", "Dispensée"),
                    ("annulee", "Annulée"),
                ],
                default="active",
                max_length=12,
            ),
        ),
    ]
