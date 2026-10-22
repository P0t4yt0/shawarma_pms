"""Automatic discount calculation for an Order.

Used by:
  * point_of_sale.views.pos_screen  -> apply_promotions(order, ...)   (saves AppliedDiscount rows at checkout)
  * point_of_sale.views.promo_preview -> preview_discounts(lines, ...) (live cart preview, saves nothing)
  * point_of_sale.services.create_order -> apply_promotions(order)

Both paths share _plan_line(), so the cart preview always equals what is charged.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F

from .models import AppliedDiscount, Promotion

TWO = Decimal("0.01")


class _Line:
    """Unsaved stand-in for an OrderItem (used by the cart preview)."""
    def __init__(self, variant, quantity, unit_price):
        self.variant, self.quantity, self.unit_price = variant, int(quantity), Decimal(str(unit_price))


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


def _candidates(gross_total, at, manual_promo_ids):
    """Live promotions for this order. MANUAL campaigns NEVER auto-apply - the cashier must select them."""
    out = []
    for p in Promotion.objects.filter(is_active=True):
        auto = p.auto_apply and p.campaign_type != Promotion.Campaign.MANUAL
        if p.is_live(at) and (auto or p.pk in manual_promo_ids) and gross_total >= p.min_order_amount:
            out.append(p)
    return out


def _plan_line(item, candidates):
    """[(promo, amount)] chosen for one line: best NON-stackable promo + every stackable one, capped at the line total."""
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
        return []
    non_stack = sorted((o for o in offers if not o[0].is_stackable), key=lambda o: (o[0].priority, -o[1]))
    chosen = non_stack[:1] + [o for o in offers if o[0].is_stackable]
    remaining, plan = item.unit_price * item.quantity, []
    for promo, amount in chosen:
        amount = min(amount, remaining)
        if amount <= 0:
            continue
        remaining -= amount
        plan.append((promo, amount))
    return plan


def preview_discounts(lines, at=None, manual_promo_ids=()):
    """lines = [(variant | None, quantity, unit_price)] -> list (same order) of [(promo, amount)]. Saves nothing."""
    items = [_Line(v, q, p) if v is not None else None for v, q, p in lines]
    gross_total = sum((i.unit_price * i.quantity for i in items if i), Decimal("0"))
    candidates = _candidates(gross_total, at, manual_promo_ids)
    return [_plan_line(i, candidates) if i else [] for i in items]


@transaction.atomic
def apply_promotions(order, at=None, manual_promo_ids=()):
    """Persist AppliedDiscount rows and OrderItem.discount_amount for every live promotion that applies."""
    items = list(order.items.select_related("variant__item"))
    gross_total = sum((i.unit_price * i.quantity for i in items), Decimal("0"))
    candidates = _candidates(gross_total, at, manual_promo_ids)

    used = set()
    for item in items:
        plan = _plan_line(item, candidates)
        if not plan:
            continue
        for promo, amount in plan:
            AppliedDiscount.objects.create(order=order, order_item=item, promotion=promo, amount=amount,
                                           description=f"{promo.name} on {item.item_name}")
            used.add(promo.pk)
        item.discount_amount = sum((a for _, a in plan), Decimal("0"))
        item.save(update_fields=["discount_amount"])

    if used:
        Promotion.objects.filter(pk__in=used).update(times_used=F("times_used") + 1)
    return order
