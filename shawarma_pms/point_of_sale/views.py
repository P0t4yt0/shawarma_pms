"""
Phase 1 views: server-rendered shells + small JSON endpoints polled every 3-5 s.

  GET  /pos/kds/                        KDS screen           (perm: view_kds)
  GET  /pos/api/kds/tickets/            open tickets         (perm: view_kds)
  GET  /pos/api/orders/ready/           cashier alert feed   (perm: view_ready_alerts)
  POST /pos/api/orders/                 create order         (perm: add_order)
  POST /pos/api/orders/<id>/status/     advance state        (perm depends on target state)
"""
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from activity_log.services import log_activity
from inventory.models import InsufficientStock
from .models import InvalidTransition, MenuVariant, Order, OrderStatusLog
from .services import create_order, latest_event_id, serialize_event, serialize_order


def _json_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------ HTML shells
@login_required
@permission_required("point_of_sale.view_kds", raise_exception=True)
def kds_screen(request):
    return render(request, "point_of_sale/kds.html", {"poll_ms": settings.KDS_POLL_SECONDS * 1000})


@login_required
@permission_required("point_of_sale.add_order", raise_exception=True)
def pos_screen(request):
    variants = MenuVariant.objects.filter(is_active=True, item__is_active=True).select_related("item")
    return render(request, "point_of_sale/pos.html",
                  {"variants": variants, "poll_ms": settings.KDS_POLL_SECONDS * 1000})


# ------------------------------------------------------------------ KDS (kitchen) API
@login_required
@permission_required("point_of_sale.view_kds", raise_exception=True)
@require_GET
def api_kds_tickets(request):
    """Open tickets, oldest first (FIFO). Also returns recently-ready tickets so the cook sees what was bumped."""
    qs = (Order.objects.filter(status__in=Order.ACTIVE_KDS_STATUSES)
          .select_related("cashier").prefetch_related("items").order_by("created_at"))
    return JsonResponse({
        "tickets": [serialize_order(o) for o in qs],
        "target_seconds": settings.KDS_TARGET_TICKET_SECONDS,
        "cursor": latest_event_id(),
        "poll_seconds": settings.KDS_POLL_SECONDS,
    })


# ------------------------------------------------------------------ Cashier alert API (Phase 1 polling target)
@login_required
@permission_required("point_of_sale.view_ready_alerts", raise_exception=True)
@require_GET
def api_ready_orders(request):
    """
    Poll every 3-5 s.
      ready   : authoritative list of orders currently ready_to_serve (survives page reloads)
      events  : status-change events with id > ?after=<cursor>  (lets the UI beep exactly once per event)
      cursor  : pass back as ?after= next time
    Add ?mine=1 to limit to orders taken by the calling cashier.
    Cost: two indexed queries -> safe at 1 request / 4 s / terminal.
    """
    mine = request.GET.get("mine") == "1"
    ready_qs = (Order.objects.filter(status=Order.Status.READY)
                .select_related("cashier").prefetch_related("items").order_by("ready_at"))
    if mine:
        ready_qs = ready_qs.filter(cashier=request.user)

    after = int(request.GET.get("after") or 0)
    events_qs = (OrderStatusLog.objects.filter(id__gt=after, to_status=Order.Status.READY)
                 .select_related("order", "order__cashier").prefetch_related("order__items").order_by("id"))
    if mine:
        events_qs = events_qs.filter(order__cashier=request.user)
    events = [serialize_event(e) for e in events_qs[:50]]

    return JsonResponse({
        "ready": [serialize_order(o) for o in ready_qs],
        "events": events,
        "cursor": max([after, latest_event_id()]),
        "poll_seconds": settings.KDS_POLL_SECONDS,
    })


# ------------------------------------------------------------------ commands
@login_required
@permission_required("point_of_sale.add_order", raise_exception=True)
@require_POST
def api_create_order(request):
    data = _json_body(request)
    try:
        order = create_order(request.user, data.get("items", []),
                             order_type=data.get("order_type", Order.Type.WALK_IN),
                             customer_name=data.get("customer_name", ""), notes=data.get("notes", ""))
    except (ValueError, KeyError, MenuVariant.DoesNotExist) as exc:
        return JsonResponse({"error": f"Invalid order: {exc}"}, status=400)
    except InsufficientStock as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse(serialize_order(order), status=201)


@login_required
@require_POST
def api_order_status(request, pk):
    """Body: {"status": "preparing" | "ready_to_serve" | "completed" | "cancelled"}"""
    order = get_object_or_404(Order, pk=pk)
    target = _json_body(request).get("status")
    if target not in Order.Status.values:
        return JsonResponse({"error": "Unknown status."}, status=400)
    perm = Order.REQUIRED_PERM.get(Order.Status(target))
    if not perm or not request.user.has_perm(perm):
        raise PermissionDenied
    try:
        log = order.transition_to(target, user=request.user)
    except InvalidTransition as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    log_activity(request.user, "status_change", "point_of_sale", order, f"{log.from_status} -> {log.to_status}",
                 seconds_in_previous=log.seconds_in_previous)
    return JsonResponse(serialize_event(log))
