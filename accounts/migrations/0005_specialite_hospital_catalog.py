from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_pin"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="specialite",
            field=models.CharField(blank=True, default="Médecine générale", max_length=80),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="catalog_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="full_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="ownership",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="department",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="commune",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="address",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="structuresante",
            name="longitude",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
