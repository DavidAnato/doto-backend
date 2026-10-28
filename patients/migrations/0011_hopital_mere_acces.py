import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0010_medecin_traitant"),
        ("accounts", "0006_pro_kyc_affiliation"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="hopital_mere",
            field=models.ForeignKey(
                blank=True,
                help_text="Hôpital mère (structure qui a créé le dossier).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="patients_hopital_mere",
                to="accounts.structuresante",
            ),
        ),
        migrations.AddField(
            model_name="patient",
            name="app_activee_le",
            field=models.DateTimeField(
                blank=True,
                help_text="Téléphone relié : les demandes extérieures reviennent au patient.",
                null=True,
                verbose_name="Application activée le",
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="route",
            field=models.CharField(
                choices=[("patient", "Patient"), ("hopital_mere", "Hôpital mère")],
                db_index=True,
                default="patient",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="requested_duration_hours",
            field=models.PositiveIntegerField(
                blank=True, null=True, verbose_name="Durée demandée (heures)"
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="decision_structure",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="access_requests_to_decide",
                to="accounts.structuresante",
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="decided_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="access_requests_decided",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="accessrequest",
            name="direct_structure",
            field=models.BooleanField(
                default=False,
                help_text="Ouverture immédiate : professionnel de l'hôpital mère ou fenêtre de transfert.",
                verbose_name="Accès direct structure",
            ),
        ),
        migrations.CreateModel(
            name="HopitalMereTransfer",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "duration_hours",
                    models.PositiveIntegerField(
                        help_text="Après confirmation, l'ancien hôpital garde l'accès déjà ouvert pendant cette durée."
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "En attente de l'autre hôpital"),
                            ("en_cours", "Transfert en cours (les deux ont accès)"),
                            ("confirme", "Confirmé"),
                            ("refuse", "Refusé"),
                            ("annule", "Annulé"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("from_approved_at", models.DateTimeField(blank=True, null=True)),
                ("to_approved_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("legacy_access_until", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transferts_confirmes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "from_approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transferts_approuves_source",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "from_structure",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transferts_sortants",
                        to="accounts.structuresante",
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transferts_hopital_mere",
                        to="patients.patient",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transferts_hopital_demandes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "to_approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transferts_approuves_destination",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "to_structure",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transferts_entrants",
                        to="accounts.structuresante",
                    ),
                ),
            ],
            options={
                "verbose_name": "Transfert d'hôpital mère",
                "verbose_name_plural": "Transferts d'hôpital mère",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="hopitalmeretransfer",
            index=models.Index(fields=["patient", "status"], name="patients_ho_patient_4c48d2_idx"),
        ),
    ]
