from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_specialite_hospital_catalog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="structuresante",
            name="type",
            field=models.CharField(
                choices=[
                    ("hopital", "Hôpital"),
                    ("clinique", "Clinique"),
                    ("polyclinique", "Polyclinique"),
                    ("centre", "Centre de santé"),
                    ("pharmacie", "Pharmacie"),
                    ("laboratoire", "Laboratoire"),
                    ("independant", "Indépendant"),
                ],
                default="clinique",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="type_exercice",
            field=models.CharField(
                blank=True,
                choices=[
                    ("etablissement_sante", "Établissement de santé"),
                    ("pharmacie", "Pharmacie"),
                    ("laboratoire", "Laboratoire"),
                    ("independant", "Indépendant"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="ville_exercice",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="user",
            name="nom_etablissement",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="user",
            name="numero_autorisation",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="user",
            name="numero_ordre",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="user",
            name="email_pro",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="user",
            name="ligne_pro",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.CreateModel(
            name="AffiliationPro",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nom_etablissement", models.CharField(blank=True, max_length=200)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("etablissement_sante", "Établissement de santé"),
                            ("pharmacie", "Pharmacie"),
                            ("laboratoire", "Laboratoire"),
                            ("independant", "Indépendant"),
                        ],
                        default="etablissement_sante",
                        max_length=32,
                    ),
                ),
                ("ville", models.CharField(blank=True, max_length=120)),
                ("numero_autorisation", models.CharField(blank=True, max_length=80)),
                ("numero_ordre", models.CharField(blank=True, max_length=80)),
                ("email_pro", models.EmailField(blank=True, max_length=254)),
                ("ligne_pro", models.CharField(blank=True, max_length=30)),
                ("principal", models.BooleanField(default=False)),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("brouillon", "Brouillon"),
                            ("en_attente", "En attente de validation"),
                            ("valide", "Validé"),
                            ("refuse", "Refusé"),
                        ],
                        db_index=True,
                        default="en_attente",
                        max_length=20,
                    ),
                ),
                ("motif_refus", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "structure",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="affiliations",
                        to="accounts.structuresante",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="affiliations",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "Affiliation professionnelle",
                "verbose_name_plural": "Affiliations professionnelles",
                "ordering": ["-principal", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="KycDossier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "subject",
                    models.CharField(
                        choices=[("patient", "Patient"), ("professionnel", "Professionnel")],
                        default="patient",
                        max_length=20,
                    ),
                ),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("brouillon", "Brouillon"),
                            ("en_attente", "En attente de validation"),
                            ("valide", "Validé"),
                            ("refuse", "Refusé"),
                        ],
                        db_index=True,
                        default="brouillon",
                        max_length=20,
                    ),
                ),
                ("motif_refus", models.TextField(blank=True)),
                ("piece_recto", models.ImageField(blank=True, null=True, upload_to="kyc/recto/")),
                ("piece_verso", models.ImageField(blank=True, null=True, upload_to="kyc/verso/")),
                ("selfie", models.ImageField(blank=True, null=True, upload_to="kyc/selfie/")),
                ("nom", models.CharField(blank=True, max_length=120)),
                ("prenom", models.CharField(blank=True, max_length=120)),
                ("date_naissance", models.DateField(blank=True, null=True)),
                ("lieu_naissance", models.CharField(blank=True, max_length=120)),
                ("npi", models.CharField(blank=True, max_length=30)),
                ("telephone", models.CharField(blank=True, max_length=30)),
                ("sexe", models.CharField(blank=True, max_length=1)),
                ("ocr_payload", models.JSONField(blank=True, default=dict)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="kyc_reviews",
                        to="accounts.user",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kyc",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "Dossier KYC",
                "verbose_name_plural": "Dossiers KYC",
                "ordering": ["-updated_at"],
            },
        ),
    ]
