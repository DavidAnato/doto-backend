from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="pin_hash",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="user",
            name="failed_pin_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="pin_locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
