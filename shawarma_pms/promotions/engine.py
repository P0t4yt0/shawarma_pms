"""Automatic discount calculation for an Order (called by point_of_sale.services.create_order)."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F

from .models import AppliedDiscount, Promotion

TWO = Decimal("0.01")


def _line_discount(promo, item):
    """Discount this promo gives on ONE order line (before caps)."""
    gross = item.unit_price * item.quantity
    if promo.kind == Promotion.Kind.PERCENT:
        return gross * promo.value / 100
    if promo.kind == Promotion.Kind.FIXED:
        return min(promo.value * item.quantity, gross)
    # BOGO: for every (buy+get) units, `get` units are discounted by value% (100 = free)
    group = promo.buy_quantity + promo.get_quantity
    free_units = (item.quantity // group) * promo.get_quantity
    return item.unit_price * free_units * (promo.value / 100)


@transaction.atomic
def apply_promotions(order, at=None, manual_promo_ids=()):
    """
    1. collect live promos (auto ones + any manually selected),
    2. compute the discount per line for each,
    3. per line keep the best NON-stackable promo (lowest priority number, then biggest discount)
       plus every stackable one, never exceeding the line's gross value,
    4. persist AppliedDiscount rows and OrderItem.discount_amount.
    """
    items = list(order.items.select_related("variant__item"))
    gross_total = sum((i.unit_price * i.quantity for i in items), Decimal("0"))

    candidates = [p for p in Promotion.objects.filter(is_active=True)
                  if p.is_live(at) and (p.auto_apply or p.pk in manual_promo_ids)
                  and gross_total >= p.min_order_amount]

    used = set()
    for item in items:
        gross = item.unit_price * item.quantity
        offers = []
        for promo in candidates:
            if not promo.covers(item.variant):
                continue
            amount = _line_discount(promo, item)
            if promo.max_discount is not None:
                amount = min(amount, promo.max_discount)
            if amount > 0:
                offers.append((promo, amount.quantize(TWO, ROUND_HALF_UP)))
        if not offers:
            continue

        non_stack = sorted((o for o in offers if not o[0].is_stackable), key=lambda o: (o[0].priority, -o[1]))
        chosen = non_stack[:1] + [o for o in offers if o[0].is_stackable]

        remaining = gross
        for promo, amount in chosen:
            amount = min(amount, remaining)
            if amount <= 0:
                continue
            remaining -= amount
            AppliedDiscount.objects.create(order=order, order_item=item, promotion=promo, amount=amount,
                                           description=f"{promo.name} on {item.item_name}")
            used.add(promo.pk)
        item.discount_amount = gross - remaining
        item.save(update_fields=["discount_amount"])

    if used:
        Promotion.objects.filter(pk__in=used).update(times_used=F("times_used") + 1)
    return order
