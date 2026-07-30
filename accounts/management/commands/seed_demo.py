"""Jeu de données de démonstration DOTO+.

Crée : structures multiples, nombreux comptes pro (tous rôles), patients
avec DotoCards, et un dossier médical complet pour le patient principal.
"""
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import StructureSante
from cards.models import DodoCard
from medical.models import Consultation, ConstanteVitale, Examen, Medicament, Ordonnance
from patients.models import Assurance, DossierMedical, Patient

User = get_user_model()


def ensure_placeholder_photo(user, initials="ID", size=320, color=(30, 55, 85)):
    """Ne plus générer de JPEG seed — avatar initiales premium côté client.

    Nettoie les anciennes photos seed_* encore en base.
    """
    from accounts.photo_utils import clear_seed_photo

    clear_seed_photo(user)
    return

# Convention MDP démo : mdp123 pour tous les comptes pro / admin.
CREDENTIALS_NOTE = """
=== Comptes de test DOTO+ ===
Mot de passe (tous les pros / admin) : mdp123
OTP (000000 en mock) : login / inscription patient
PIN session pro : 12345

Admin
  admin / mdp123

Médecins
  medecin  / mdp123
  medecin2 / mdp123
  medecin3 / mdp123

Infirmiers
  infirmier  / mdp123
  infirmier2 / mdp123
  infirmier3 / mdp123

Pharmaciens
  pharmacien  / mdp123
  pharmacien2 / mdp123

Laborantins
  laborantin  / mdp123
  laborantin2 / mdp123

Ambulanciers
  ambulancier  / mdp123
  ambulancier2 / mdp123
  ambulancier3 / mdp123

Réceptionnistes
  reception  / mdp123
  reception2 / mdp123

Patients (DotoPlus) — téléphone + OTP (mock 000000)
  +229 97 45 12 88 · NPI 1200478821 (identification Hub, pas login)
  +229 97 11 22 33 · NPI 1200112233
  +229 96 55 44 33 · NPI 1200998877
  +229 95 33 22 11 · NPI 1200334455
  PIN déverrouillage (optionnel patient) : 12345
  PIN pro (obligatoire session) : 12345
""".strip()


class Command(BaseCommand):
    help = "Charge des données de démonstration réalistes (multi-rôles, multi-structures)."

    def handle(self, *args, **options):
        self.stdout.write("Initialisation des données de démonstration DOTO+…")

        cnhu, _ = StructureSante.objects.get_or_create(
            code_structure="STRUCT-CNHU",
            defaults=dict(
                nom="CNHU Cotonou",
                type=StructureSante.Type.HOPITAL,
                localisation="Cotonou",
                telephone="+229 21 30 01 55",
            ),
        )
        cocotiers, _ = StructureSante.objects.get_or_create(
            code_structure="STRUCT-COCO",
            defaults=dict(
                nom="Polyclinique Les Cocotiers",
                type=StructureSante.Type.POLYCLINIQUE,
                localisation="Cotonou",
            ),
        )
        pharma_abc, _ = StructureSante.objects.get_or_create(
            code_structure="STRUCT-PHARM",
            defaults=dict(
                nom="Pharmacie ABC Cotonou",
                type=StructureSante.Type.PHARMACIE,
                localisation="Cotonou",
            ),
        )
        labo_bs, _ = StructureSante.objects.get_or_create(
            code_structure="STRUCT-LABO",
            defaults=dict(
                nom="Labo Bénin-Santé",
                type=StructureSante.Type.LABORATOIRE,
                localisation="Cotonou",
            ),
        )
        samu, _ = StructureSante.objects.get_or_create(
            code_structure="STRUCT-SAMU",
            defaults=dict(
                nom="SAMU / Urgences mobiles",
                type=StructureSante.Type.CENTRE,
                localisation="Cotonou",
                telephone="+229 21 30 00 00",
            ),
        )

        # username, prenom, nom, role, password, structure principale
        DEMO_PWD = "mdp123"
        accounts = [
            ("admin", "Awa", "GNONLONFOUN", User.Role.ADMIN, DEMO_PWD, cnhu),
            ("medecin", "Rosine", "FANOU", User.Role.MEDECIN, DEMO_PWD, cnhu),
            ("medecin2", "Jean", "HOUNKPE", User.Role.MEDECIN, DEMO_PWD, cocotiers),
            ("medecin3", "Fatou", "DOSSOU", User.Role.MEDECIN, DEMO_PWD, cnhu),
            ("infirmier", "Paul", "SOSSA", User.Role.INFIRMIER, DEMO_PWD, cnhu),
            ("infirmier2", "Nadia", "ADJOVI", User.Role.INFIRMIER, DEMO_PWD, cocotiers),
            ("infirmier3", "Marc", "GBENOU", User.Role.INFIRMIER, DEMO_PWD, cnhu),
            ("pharmacien", "Awa", "KONE", User.Role.PHARMACIEN, DEMO_PWD, pharma_abc),
            ("pharmacien2", "Serge", "HOUNSOU", User.Role.PHARMACIEN, DEMO_PWD, cnhu),
            ("laborantin", "Akim", "BELLO", User.Role.LABORANTIN, DEMO_PWD, labo_bs),
            ("laborantin2", "Esther", "TOKO", User.Role.LABORANTIN, DEMO_PWD, cnhu),
            ("ambulancier", "Kodjo", "MENSAH", User.Role.AMBULANCIER, DEMO_PWD, samu),
            ("ambulancier2", "Blandine", "KOFFI", User.Role.AMBULANCIER, DEMO_PWD, samu),
            ("ambulancier3", "Yao", "AGBO", User.Role.AMBULANCIER, DEMO_PWD, cnhu),
            ("reception", "Carole", "AZON", User.Role.RECEPTIONNISTE, DEMO_PWD, cnhu),
            ("reception2", "Ibrahim", "YAYA", User.Role.RECEPTIONNISTE, DEMO_PWD, cocotiers),
        ]

        created_users = {}
        for username, prenom, nom, role, pwd, structure in accounts:
            user, is_new = User.objects.get_or_create(
                username=username,
                defaults=dict(
                    first_name=prenom,
                    last_name=nom,
                    role=role,
                    telephone="+229 97 00 00 00",
                    structure_principale=structure,
                ),
            )
            user.set_password(pwd)
            user.role = role
            user.structure_principale = structure
            user.first_name = prenom
            user.last_name = nom
            if role == User.Role.ADMIN:
                user.is_staff = True
                user.is_superuser = True
            user.save()
            if not user.has_pin:
                user.set_pin("12345")
            user.structures.add(structure, cnhu)
            created_users[username] = user
            ensure_placeholder_photo(user, initials=f"{prenom[:1]}{nom[:1]}".upper() or "P")

        medecin = created_users["medecin"]
        infirmier = created_users["infirmier"]
        laborantin = created_users["laborantin"]

        # ─── Patients mobiles ─────────────────────────────────────────────
        patients_data = [
            {
                "phone": "+229 97 45 12 88",
                "npi": "1200478821",
                "nom": "ADJOVI",
                "prenom": "Kofi Emmanuel",
                "naissance": date(1999, 12, 12),
                "sexe": "M",
                "gs": "A+",
                "electro": "AS",
                "urgence_nom": "Marie Adjovi",
                "urgence_lien": "Épouse",
                "full": True,
            },
            {
                "phone": "+229 97 11 22 33",
                "npi": "1200112233",
                "nom": "HOUNKPATIN",
                "prenom": "Amina",
                "naissance": date(1988, 3, 21),
                "sexe": "F",
                "gs": "O+",
                "electro": "AA",
                "urgence_nom": "Kwame Hounkpatin",
                "urgence_lien": "Époux",
                "full": False,
            },
            {
                "phone": "+229 96 55 44 33",
                "npi": "1200998877",
                "nom": "ZINSOU",
                "prenom": "Emile",
                "naissance": date(1975, 7, 8),
                "sexe": "M",
                "gs": "B+",
                "electro": "Non identifié",
                "urgence_nom": "Grace Zinsou",
                "urgence_lien": "Fille",
                "full": False,
            },
            {
                "phone": "+229 95 33 22 11",
                "npi": "1200334455",
                "nom": "ALIDOU",
                "prenom": "Fatima",
                "naissance": date(2001, 1, 30),
                "sexe": "F",
                "gs": "Non identifié",
                "electro": "SC",
                "urgence_nom": "Issa Alidou",
                "urgence_lien": "Père",
                "full": False,
            },
        ]

        main_patient = None
        for pdata in patients_data:
            patient_user, is_new = User.objects.get_or_create(
                username=pdata["phone"],
                defaults=dict(
                    first_name=pdata["prenom"],
                    last_name=pdata["nom"],
                    role=User.Role.PATIENT,
                    telephone=pdata["phone"],
                ),
            )
            if is_new:
                patient_user.set_unusable_password()
                patient_user.save()
            else:
                # Migration : comptes démo passent en OTP-only
                if patient_user.has_usable_password():
                    patient_user.set_unusable_password()
                    patient_user.save(update_fields=["password"])

            # Récupérer le patient par user (évite conflit UNIQUE user_id
            # quand l'ancien NPI BJ-… diffère du NPI 10 chiffres).
            patient = Patient.objects.filter(user=patient_user).first()
            if patient is None:
                patient = Patient.objects.filter(npi=pdata["npi"]).first()
            if patient is None:
                patient = Patient.objects.create(
                    npi=pdata["npi"],
                    user=patient_user,
                    nom=pdata["nom"],
                    prenom=pdata["prenom"],
                    date_naissance=pdata["naissance"],
                    sexe=pdata["sexe"],
                    groupe_sanguin=pdata["gs"],
                    electrophorese=pdata.get("electro", "Non identifié"),
                    telephone=pdata["phone"],
                    npi_verifie_anip=True,
                    contact_urgence_nom=pdata["urgence_nom"],
                    contact_urgence_lien=pdata["urgence_lien"],
                    tel_urgence=pdata["phone"],
                )
            else:
                patient.npi = pdata["npi"]
                patient.user = patient_user
                patient.nom = pdata["nom"]
                patient.prenom = pdata["prenom"]
                patient.date_naissance = pdata["naissance"]
                patient.sexe = pdata["sexe"]
                patient.telephone = pdata["phone"]
                patient.contact_urgence_nom = pdata["urgence_nom"]
                patient.contact_urgence_lien = pdata["urgence_lien"]
                patient.tel_urgence = pdata["phone"]
                patient.npi_verifie_anip = True
            patient.groupe_sanguin = pdata["gs"]
            patient.electrophorese = pdata.get("electro", "Non identifié")
            patient.save()
            # PIN démo 5 chiffres (optionnel — on le pose pour faciliter les tests)
            if not patient.has_pin:
                patient.set_pin("12345")
            else:
                # Migrer l'ancien PIN 6 chiffres seed
                if not patient.check_pin("12345"):
                    patient.set_pin("12345")

            ensure_placeholder_photo(
                patient_user,
                initials=f"{pdata['prenom'][:1]}{pdata['nom'][:1]}".upper(),
                color=(8, 80, 65) if pdata["full"] else (30, 55, 85),
            )
            # Recharger patient.photo si sync
            patient.refresh_from_db()

            DossierMedical.objects.update_or_create(
                patient=patient,
                defaults=dict(
                    antecedents="Antécédents de démonstration." if not pdata["full"] else "Appendicectomie 2025.",
                    allergies=["Pénicilline", "Aspirine"] if pdata["full"] else ["Non identifié"],
                    maladies_chroniques=(
                        [
                            {"nom": "Hypertension", "depuis": "2019"},
                            {"nom": "Diabète T2", "depuis": "2021"},
                        ]
                        if pdata["full"]
                        else []
                    ),
                ),
            )

            Assurance.objects.update_or_create(
                patient=patient,
                defaults=dict(
                    assureur="NSIA Bénin",
                    num_police=f"NSIA-2024-BJ-{pdata['npi'][-7:]}",
                    type_couverture="Famille" if pdata["full"] else "Individuel",
                    valide_du=date(2025, 1, 1),
                    valide_au=date(2025, 12, 31),
                    droits_valides=True,
                    garanties=Assurance.garanties_par_defaut(),
                ),
            )

            if not patient.dodocards.filter(statut=DodoCard.Statut.ACTIVE).exists():
                DodoCard.issue(patient, cvv="482")

            if pdata["full"]:
                main_patient = patient

        patient = main_patient
        if patient is None:
            patient = Patient.objects.get(npi="1200478821")

        # ─── Dossier médical complet (patient principal) ──────────────────
        if not patient.consultations.exists():
            c1 = Consultation.objects.create(
                patient=patient,
                structure=cocotiers,
                medecin=medecin,
                date=timezone.make_aware(datetime(2025, 6, 12, 9, 0)),
                type=Consultation.Type.CONSULTATION,
                diagnostic="Tension artérielle — HTA confirmée",
                notes="PA 155/95 mmHg. Amlodipine 5 mg. Contrôle dans 3 semaines.",
            )
            Consultation.objects.create(
                patient=patient,
                structure=cnhu,
                medecin=medecin,
                date=timezone.make_aware(datetime(2025, 4, 3, 10, 30)),
                type=Consultation.Type.CONSULTATION,
                diagnostic="Suivi diabète type 2 — glycémie stable",
                notes="HbA1c 7.2%. Metformine 500 mg maintenue.",
            )
            Consultation.objects.create(
                patient=patient,
                structure=cnhu,
                medecin=medecin,
                date=timezone.make_aware(datetime(2025, 1, 15, 8, 0)),
                type=Consultation.Type.HOSPITALISATION,
                diagnostic="Hospitalisation — appendicite aiguë",
                notes="Appendicectomie laparoscopique. Sortie le 18/01.",
            )

            o1 = Ordonnance.objects.create(
                patient=patient,
                medecin=medecin,
                structure=pharma_abc,
                consultation=c1,
                date=date(2025, 6, 12),
                statut=Ordonnance.Statut.ACTIVE,
                instructions="Prendre le matin. Régime hyposodé.",
            )
            Medicament.objects.create(
                ordonnance=o1,
                nom="Amlodipine 5mg",
                dosage="1 comprimé",
                frequence="Une fois par jour",
                duree_jours=30,
            )
            o2 = Ordonnance.objects.create(
                patient=patient,
                medecin=medecin,
                structure=cnhu,
                date=date(2025, 4, 3),
                statut=Ordonnance.Statut.ACTIVE,
            )
            Medicament.objects.create(
                ordonnance=o2,
                nom="Metformine 500mg",
                dosage="2 comprimés",
                frequence="Deux fois par jour",
                duree_jours=30,
            )

            Examen.objects.create(
                patient=patient,
                categorie=Examen.Categorie.ANALYSES,
                type_examen="NFS complète",
                laboratoire="Labo Bénin-Santé",
                laborantin=laborantin,
                medecin_prescripteur=medecin,
                date=date(2025, 6, 5),
                statut=Examen.Statut.NORMAL,
                resultat_texte="Paramètres dans les normes.",
            )
            Examen.objects.create(
                patient=patient,
                categorie=Examen.Categorie.ANALYSES,
                type_examen="Glycémie à jeun",
                laboratoire="CNHU Cotonou",
                laborantin=laborantin,
                medecin_prescripteur=medecin,
                date=date(2025, 4, 3),
                statut=Examen.Statut.ELEVE,
                resultat_texte="6.8 mmol/L.",
            )

            ConstanteVitale.objects.create(
                patient=patient,
                infirmier=infirmier,
                tension_systolique=155,
                tension_diastolique=95,
                temperature=37.0,
                poids=78.5,
                glycemie=6.80,
            )

        n_pros = User.objects.filter(
            role__in=[
                User.Role.MEDECIN,
                User.Role.INFIRMIER,
                User.Role.PHARMACIEN,
                User.Role.LABORANTIN,
                User.Role.AMBULANCIER,
                User.Role.RECEPTIONNISTE,
                User.Role.ADMIN,
            ]
        ).count()
        n_patients = Patient.objects.count()
        n_cards = DodoCard.objects.filter(statut=DodoCard.Statut.ACTIVE).count()

        # ─── RDV + blocages démo ──────────────────────────────────────────
        from patients.models import AccessBlock, Appointment

        now = timezone.now()
        if not Appointment.objects.filter(patient=patient).exists():
            Appointment.objects.create(
                patient=patient,
                structure=cnhu,
                professionnel=medecin,
                created_by=created_users["reception"],
                debut=now + timedelta(days=3, hours=2),
                fin=now + timedelta(days=3, hours=2, minutes=30),
                motif="Contrôle tension / HTA",
                statut=Appointment.Statut.CONFIRME,
            )
            Appointment.objects.create(
                patient=patient,
                structure=cocotiers,
                professionnel=created_users["medecin2"],
                created_by=created_users["reception2"],
                debut=now + timedelta(days=10),
                fin=now + timedelta(days=10, minutes=45),
                motif="Suivi diabète",
                statut=Appointment.Statut.PLANIFIE,
            )
            Appointment.objects.create(
                patient=patient,
                structure=cnhu,
                professionnel=medecin,
                created_by=medecin,
                debut=now - timedelta(days=14),
                fin=now - timedelta(days=14) + timedelta(minutes=30),
                motif="Consultation générale",
                statut=Appointment.Statut.TERMINE,
            )

        # Exemple de blocage (patient principal bloque ambulancier3 — démo blacklist)
        AccessBlock.objects.update_or_create(
            patient=patient,
            blocked_user=created_users["ambulancier3"],
            blocked_structure=None,
            defaults=dict(
                reason="Démo — accès bloqué définitivement",
                active=True,
            ),
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Données prêtes — {n_pros} pros, {n_patients} patients, {n_cards} DotoCards actives.\n\n"
                f"{CREDENTIALS_NOTE}"
            )
        )
