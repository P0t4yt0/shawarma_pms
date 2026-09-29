"""Business logic + serializers. Views and (Phase 2) WebSocket consumers both call these."""
from django.db import transaction

from activity_log.services import log_activity
from .models import MenuVariant, Order, OrderItem, OrderStatusLog


# ------------------------------------------------------------------ order creation
@transaction.atomic
def create_order(cashier, lines, order_type=Order.Type.WALK_IN, customer_name="", notes="", at=None):
    """
    lines = [{"variant_id": 3, "quantity": 2, "notes": "no onions"}, ...]
    Steps: capture ticket -> apply promotions -> total -> deduct raw ingredients via each recipe.
    Everything is atomic: if stock blocking is enabled and something is short, nothing is saved.
    """
    if not lines:
        raise ValueError("An order needs at least one item.")
    order = Order.objects.create(order_type=order_type, cashier=cashier, customer_name=customer_name, notes=notes)
    for line in lines:
        variant = MenuVariant.objects.select_related("item").get(pk=line["variant_id"], is_active=True,
                                                                  item__is_active=True)
        OrderItem.objects.create(order=order, variant=variant, item_name=str(variant),
                                 quantity=int(line.get("quantity", 1)), unit_price=variant.price,
                                 notes=line.get("notes", "")[:200])

    from promotions.engine import apply_promotions          # local import: no app-level cycle
    apply_promotions(order, at=at)
    order.recalculate_totals()

    for item in order.items.select_related("variant"):
        recipe = getattr(item.variant, "recipe", None)
        if recipe and recipe.is_active:
            recipe.deduct_stock(item.quantity, order=order, user=cashier)

    log_activity(cashier, "create", "point_of_sale", order, f"Order {order.order_number} total {order.total}")
    return order


def restore_order_stock(order, user=None):
    for item in order.items.select_related("variant"):
        recipe = getattr(item.variant, "recipe", None)
        if recipe and recipe.is_active:
            recipe.restore_stock(item.quantity, order=order, user=user)


# ------------------------------------------------------------------ serializers (shared by polling + websockets)
def serialize_order(order):
    return {
        "id": order.id,
        "order_number": order.order_number,
        "order_type": order.order_type,
        "status": order.status,
        "customer_name": order.customer_name,
        "notes": order.notes,
        "cashier": order.cashier.get_username() if order.cashier else None,
        "total": str(order.total),
        "created_at": order.created_at.isoformat(),
        "preparing_at": order.preparing_at.isoformat() if order.preparing_at else None,
        "ready_at": order.ready_at.isoformat() if order.ready_at else None,
        "elapsed_seconds": order.elapsed_seconds,
        "items": [{"name": i.item_name, "quantity": i.quantity, "notes": i.notes} for i in order.items.all()],
    }


def serialize_event(log: OrderStatusLog):
    return {
        "event_id": log.id,                    # cursor for polling; ordering key for websockets
        "type": "order.status_changed",
        "from": log.from_status,
        "to": log.to_status,
        "at": log.changed_at.isoformat(),
        "order": serialize_order(log.order),
    }


def latest_event_id():
    last = OrderStatusLog.objects.order_by("-id").values_list("id", flat=True).first()
    return last or 0
