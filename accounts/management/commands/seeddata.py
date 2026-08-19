"""Alias Render / prod : `python manage.py seeddata` → même jeu que seed_demo."""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Charge les données de démonstration (structures, pros, patients, dossier médical). "
        "Alias de seed_demo - prévu pour le build / shell Render."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Compatibilité CI (aucune invite interactive).",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("seeddata → seed_demo…"))
        call_command("seed_demo")
        self.stdout.write(self.style.SUCCESS("seeddata terminé."))
