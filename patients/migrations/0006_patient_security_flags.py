from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0005_patient_electrophorese_groupe_ni"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="require_unlock",
            field=models.BooleanField(
                default=False,
                help_text="Si vrai : PIN ou biométrie à chaque ouverture de l'app.",
                verbose_name="Exiger déverrouillage",
            ),
        ),
        migrations.AddField(
            model_name="patient",
            name="urgence_when_locked",
            field=models.BooleanField(
                default=True,
                help_text="Autorise l'accès au mode Urgence depuis l'écran de verrouillage.",
                verbose_name="Urgence si verrouillé",
            ),
        ),
    ]
