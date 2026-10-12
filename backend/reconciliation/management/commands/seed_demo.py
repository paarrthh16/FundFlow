from django.core.management.base import BaseCommand

from reconciliation.services.seed import seed_demo


class Command(BaseCommand):
    help = "Create the demo organizations, capital calls and line items. Safe to run repeatedly."

    def handle(self, *args, **options):
        report = seed_demo()

        for label in sorted(set(report.created) | set(report.existing)):
            self.stdout.write(
                f"{label}: {report.created[label]} created, {report.existing[label]} already present"
            )
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(warning))
        self.stdout.write(self.style.SUCCESS("seed_demo finished."))
