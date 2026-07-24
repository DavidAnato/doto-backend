from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0004_appointment_accessblock"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="electrophorese",
            field=models.CharField(
                blank=True,
                help_text="Phénotype Hb (AA, AS, SS…) ou Non identifié / texte libre.",
                max_length=40,
                verbose_name="Électrophorèse Hb",
            ),
        ),
        migrations.AlterField(
            model_name="patient",
            name="groupe_sanguin",
            field=models.CharField(
                blank=True,
                choices=[
                    ("A+", "A+"),
                    ("A-", "A-"),
                    ("B+", "B+"),
                    ("B-", "B-"),
                    ("AB+", "AB+"),
                    ("AB-", "AB-"),
                    ("O+", "O+"),
                    ("O-", "O-"),
                    ("Non identifié", "Non identifié"),
                ],
                max_length=20,
            ),
        ),
    ]
