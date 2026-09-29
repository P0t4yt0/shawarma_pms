from decimal import Decimal

from django.conf import settings
from django.db import transaction

from activity_log.services import log_activity
from .models import Payment, PaymentMethod, Receipt


@transaction.atomic
def record_payment(order, method_code, amount=None, tendered=None, reference="", user=None):
    """Take one payment toward an order. Call repeatedly for split payments (e.g. cash + GCash)."""
    from point_of_sale.models import Order
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == Order.Status.CANCELLED:
        raise ValueError("Cannot take payment for a cancelled order.")
    method = PaymentMethod.objects.get(code=method_code, is_active=True)
    due = order.balance_due
    if due <= 0:
        raise ValueError("Order is already fully paid.")
    amount = Decimal(str(amount)) if amount is not None else due
    amount = min(amount, due)
    change = Decimal("0")
    if method.kind == PaymentMethod.Kind.CASH and tendered is not None:
        tendered = Decimal(str(tendered))
        change = tendered - amount
    payment = Payment(order=order, method=method, amount=amount, tendered=tendered, change_given=max(change, 0),
                      reference_number=reference, received_by=user)
    payment.full_clean(exclude=["order", "received_by"])
    payment.save()
    log_activity(user, "payment", "billing_payment", payment, f"{method.name} {amount} for {order.order_number}")
    if order.balance_due == 0:
        issue_receipt(order, user)
    return payment


@transaction.atomic
def issue_receipt(order, user=None):
    if hasattr(order, "receipt"):
        return order.receipt
    paid = order.payments.filter(status="completed").select_related("method")
    snapshot = {
        "store": settings.POS_STORE_NAME,
        "order_number": order.order_number,
        "items": [{"name": i.item_name, "quantity": i.quantity, "unit_price": str(i.unit_price),
                   "net": str(i.net)} for i in order.items.all()],
        "subtotal": str(order.subtotal), "discount_total": str(order.discount_total),
        "total": str(order.total), "tax_total": str(order.tax_total),
        "payments": [{"method": p.method.name, "amount": str(p.amount), "reference": p.reference_number}
                     for p in paid],
        "change": str(sum((p.change_given for p in paid), Decimal("0"))),
    }
    return Receipt.objects.create(order=order, receipt_number=f"R-{order.order_number}", issued_by=user,
                                  snapshot=snapshot)
