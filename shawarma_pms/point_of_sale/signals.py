"""
Domain event: fired once (after DB commit) for every order status change.

Phase 1 : nobody needs to listen - JS polls the DB-backed event feed.
Phase 2 : flip settings.KDS_REALTIME = True; the receiver below pushes the SAME payload
          to Channels groups, so no model or view code changes.
"""
from django.conf import settings
from django.dispatch import Signal, receiver

order_status_changed = Signal()      # kwargs: order, log

KDS_GROUP = "kds"           # kitchen screens
CASHIER_GROUP = "cashiers"  # POS terminals


@receiver(order_status_changed)
def push_to_websocket(sender, order, log, **kwargs):
    if not getattr(settings, "KDS_REALTIME", False):
        return                                    # Phase 1: polling only
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from .services import serialize_event

    layer = get_channel_layer()
    payload = serialize_event(log)
    message = {"type": "order.event", "payload": payload}      # -> consumer.order_event()
    async_to_sync(layer.group_send)(KDS_GROUP, message)
    if log.to_status == "ready_to_serve":
        async_to_sync(layer.group_send)(CASHIER_GROUP, message)
