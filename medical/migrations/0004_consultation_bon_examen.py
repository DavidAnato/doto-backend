import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("patients", "0008_accessrequest_cancelled"),
        ("medical", "0003_consultation_examen_annule"),
        ("accounts", "0005_specialite_hospital_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="consultation",
            name="appointment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="consultations",
                to="patients.appointment",
            ),
        ),
        migrations.AddField(
            model_name="consultation",
            name="specialite",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="consultation",
            name="motif",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="consultation",
            name="extra",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="consultation",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name="consultation",
            name="type",
            field=models.CharField(
                choices=[
                    ("consultation", "Consultation"),
                    ("hospitalisation", "Hospitalisation"),
                    ("urgence", "Urgence"),
                    ("suivi", "Suivi/Contrôle"),
                    ("chirurgie", "Chirurgie"),
                ],
                default="consultation",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ordonnance",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="medicament",
            name="forme",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="medicament",
            name="quantite",
            field=models.CharField(blank=True, help_text="Quantité à délivrer, ex. 1 boîte", max_length=80),
        ),
        migrations.AddField(
            model_name="medicament",
            name="unites_par_prise",
            field=models.CharField(blank=True, help_text="Ex. 2 comprimés", max_length=40),
        ),
        migrations.AddField(
            model_name="medicament",
            name="frequence_par_jour",
            field=models.CharField(blank=True, help_text="Ex. 3/jour", max_length=40),
        ),
        migrations.AddField(
            model_name="medicament",
            name="moment",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="medicament",
            name="instructions",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="examen",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.CreateModel(
            name="BonExamen",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("laboratoire_nom", models.CharField(blank=True, max_length=150)),
                ("motif", models.CharField(blank=True, max_length=255)),
                ("observations", models.TextField(blank=True)),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("demande", "Demandé"),
                            ("recu", "Reçu"),
                            ("en_cours", "En cours"),
                            ("resultat_disponible", "Résultat disponible"),
                            ("cloture", "Clôturé"),
                        ],
                        db_index=True,
                        default="demande",
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "laboratoire",
                    models.ForeignKey(
                        blank=True,
                        help_text="Laboratoire / structure destinataire.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="bons_examen_labo",
                        to="accounts.structuresante",
                    ),
                ),
                (
                    "medecin",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="bons_examen_prescrits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bons_examen",
                        to="patients.patient",
                    ),
                ),
                (
                    "structure",
                    models.ForeignKey(
                        blank=True,
                        help_text="Structure du médecin prescripteur.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="bons_examen_origine",
                        to="accounts.structuresante",
                    ),
                ),
            ],
            options={
                "verbose_name": "Bon d'examen",
                "verbose_name_plural": "Bons d'examen",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BonExamenLigne",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type_examen", models.CharField(max_length=120)),
                (
                    "categorie",
                    models.CharField(
                        choices=[
                            ("analyses", "Analyses"),
                            ("imagerie", "Imagerie"),
                            ("autres", "Autres"),
                        ],
                        default="analyses",
                        max_length=12,
                    ),
                ),
                ("code", models.CharField(blank=True, max_length=40)),
                (
                    "bon",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lignes",
                        to="medical.bonexamen",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="examen",
            name="bon",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="resultats",
                to="medical.bonexamen",
            ),
        ),
        migrations.AddField(
            model_name="examen",
            name="ligne",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="resultats",
                to="medical.bonexamenligne",
            ),
        ),
    ]
