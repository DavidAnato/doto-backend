# Soft-cancel: Consultation.annule + Examen.annule

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("medical", "0002_ordonnance_annulee"),
    ]

    operations = [
        migrations.AddField(
            model_name="consultation",
            name="annule",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="examen",
            name="annule",
            field=models.BooleanField(default=False),
        ),
    ]
