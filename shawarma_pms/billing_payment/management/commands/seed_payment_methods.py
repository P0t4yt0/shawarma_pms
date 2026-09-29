from django.core.management.base import BaseCommand
from billing_payment.models import PaymentMethod


class Command(BaseCommand):
    help = "Seed cash, e-wallet and card payment methods."

    def handle(self, *args, **opts):
        for code, name, kind, ref in [
            ("cash", "Cash", "cash", False), ("gcash", "GCash", "ewallet", True),
            ("maya", "Maya", "ewallet", True), ("card", "Credit/Debit Card", "card", True),
        ]:
            PaymentMethod.objects.update_or_create(code=code, defaults=dict(name=name, kind=kind, requires_reference=ref))
        self.stdout.write(self.style.SUCCESS("Payment methods seeded."))
