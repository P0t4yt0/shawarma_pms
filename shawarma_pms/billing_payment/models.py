"""billing_payment app: payment methods, (split) payments and receipts for point_of_sale.Order."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class PaymentMethod(models.Model):
    class Kind(models.TextChoices):
        CASH = "cash", "Cash"
        EWALLET = "ewallet", "E-wallet"
        CARD = "card", "Card"

    code = models.SlugField(unique=True)                    # cash, gcash, maya, visa ...
    name = models.CharField(max_length=50)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    requires_reference = models.BooleanField(default=False, help_text="Ref/approval no. is mandatory")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Payment(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        REFUNDED = "refunded", "Refunded"
        VOIDED = "voided", "Voided"

    order = models.ForeignKey("point_of_sale.Order", on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)          # amount applied to the bill
    tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)   # cash handed over
    change_given = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reference_number = models.CharField(max_length=60, blank=True)         # e-wallet / card approval ref
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED, db_index=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-paid_at"]
        permissions = [("refund_payment", "Can refund or void payments")]

    def __str__(self):
        return f"{self.method} {self.amount} for {self.order}"

    def clean(self):
        if self.method.requires_reference and not self.reference_number:
            raise ValidationError(f"{self.method.name} payments need a reference number.")
        if self.method.kind == PaymentMethod.Kind.CASH and self.tendered is not None and self.tendered < self.amount:
            raise ValidationError("Cash tendered is less than the amount.")


class Receipt(models.Model):
    """Immutable snapshot so a reprint always matches what the customer originally got."""
    order = models.OneToOneField("point_of_sale.Order", on_delete=models.PROTECT, related_name="receipt")
    receipt_number = models.CharField(max_length=30, unique=True)
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    snapshot = models.JSONField(default=dict)
    print_count = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return self.receipt_number

    def render_text(self, width=32):
        s = self.snapshot
        line = "-" * width
        out = [s["store"].center(width), f"Receipt {self.receipt_number}".center(width),
               f"{self.issued_at:%Y-%m-%d %H:%M}".center(width), line]
        for i in s["items"]:
            out.append(f'{i["quantity"]} x {i["name"]}'[: width - 10].ljust(width - 10) + f'{Decimal(i["net"]):>10,.2f}')
        out += [line, f'{"Subtotal":<{width-10}}{Decimal(s["subtotal"]):>10,.2f}']
        if Decimal(s["discount_total"]):
            out.append(f'{"Discounts":<{width-10}}{-Decimal(s["discount_total"]):>10,.2f}')
        out.append(f'{"TOTAL":<{width-10}}{Decimal(s["total"]):>10,.2f}')
        out.append(f'{"VAT incl.":<{width-10}}{Decimal(s["tax_total"]):>10,.2f}')
        out.append(line)
        for p in s["payments"]:
            out.append(f'{p["method"]:<{width-10}}{Decimal(p["amount"]):>10,.2f}')
        if Decimal(s["change"]):
            out.append(f'{"Change":<{width-10}}{Decimal(s["change"]):>10,.2f}')
        return "\n".join(out)
