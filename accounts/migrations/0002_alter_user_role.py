from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("patient", "Patient"),
                    ("medecin", "Médecin"),
                    ("infirmier", "Infirmier"),
                    ("pharmacien", "Pharmacien"),
                    ("laborantin", "Laborantin"),
                    ("ambulancier", "Ambulancier"),
                    ("receptionniste", "Réceptionniste"),
                    ("admin", "Admin structure"),
                ],
                default="medecin",
                max_length=20,
            ),
        ),
    ]
