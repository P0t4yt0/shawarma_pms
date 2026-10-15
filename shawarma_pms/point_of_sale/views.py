"""
Phase 1 views: server-rendered shells + small JSON endpoints polled every 3-5 s.

  GET  /pos/kds/                        KDS screen           (perm: view_kds)
  GET  /pos/api/kds/tickets/            open tickets         (perm: view_kds)
  GET  /pos/api/orders/ready/           cashier alert feed   (perm: view_ready_alerts)
  POST /pos/api/orders/                 create order         (perm: add_order)
  POST /pos/api/orders/<id>/status/     advance state        (perm depends on target state)
"""
import json
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from activity_log.services import log_activity
from inventory.models import InsufficientStock
from .models import InvalidTransition, MenuVariant, Order, OrderStatusLog
from .services import create_order, latest_event_id, serialize_event, serialize_order
from django.shortcuts import render
from django.db import transaction
from .models import Category, MenuVariant, Order, OrderItem, OrderCounter
from billing_payment.models import PaymentMethod, Payment, Receipt


def _json_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------ HTML shells
@login_required
@permission_required("point_of_sale.view_kds", raise_exception=True)
def kds_screen(request):
    # Kunin lahat ng aktibong orders na kailangang i-prepare at i-serve
    active_orders = Order.objects.filter(
        status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY]
    ).prefetch_related('items').order_by('created_at')

    # Hatiin natin sa tatlong lists para madaling i-loop sa HTML
    pending_orders = [o for o in active_orders if o.status == Order.Status.PENDING]
    preparing_orders = [o for o in active_orders if o.status == Order.Status.PREPARING]
    ready_orders = [o for o in active_orders if o.status == Order.Status.READY]

    return render(request, 'kds.html', {
        'pending_orders': pending_orders,
        'preparing_orders': preparing_orders,
        'ready_orders': ready_orders,
    })

@login_required
@permission_required("point_of_sale.view_kds", raise_exception=True)
def kds_update_status(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            order_number = data.get('order_number')
            action = data.get('action')

            order = Order.objects.get(order_number=order_number)
            user = request.user if request.user.is_authenticated else None

            # Gamitin ang state machine ng Order model
            if action == 'start':
                order.transition_to(Order.Status.PREPARING, user=user)
            elif action == 'ready':
                order.transition_to(Order.Status.READY, user=user)
            elif action == 'complete':
                order.transition_to(Order.Status.COMPLETED, user=user)
            else:
                return JsonResponse({'success': False, 'error': 'Invalid action'})

            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@permission_required("point_of_sale.add_order", raise_exception=True)
def pos_screen(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            cart_items = data.get('items', [])
            
            if not cart_items:
                return JsonResponse({"success": False, "error": "Cart is empty"})

            with transaction.atomic():
                # 1. Create the Order
                order = Order.objects.create(
                    cashier=request.user if request.user.is_authenticated else None,
                    status=Order.Status.PENDING,
                    order_type=Order.Type.WALK_IN
                )
                
                # 2. Add Items
                for item in cart_items:
                    variant = MenuVariant.objects.get(id=item['id'])
                    OrderItem.objects.create(
                        order=order,
                        variant=variant,
                        item_name=f"{variant.item.name} ({variant.name})",
                        quantity=item['qty'],
                        unit_price=variant.price
                    )
                order.recalculate_totals(save=True)
                
                # 3. Create the Payment Record
                pay_method = data.get('payment_method')
                tendered = Decimal(str(data.get('tendered', 0)))
                provider = data.get('provider', '')
                ref_num = data.get('refNum', '')
                
                if pay_method == 'cash':
                    pm, _ = PaymentMethod.objects.get_or_create(code='cash', defaults={'name': 'Cash', 'kind': PaymentMethod.Kind.CASH})
                    Payment.objects.create(
                        order=order, method=pm, amount=order.total, tendered=tendered,
                        change_given=tendered - order.total, received_by=order.cashier
                    )
                else:
                    pm_code = provider.lower().replace(' ', '') if provider else 'ewallet'
                    pm_name = provider if provider else 'E-Wallet'
                    pm, _ = PaymentMethod.objects.get_or_create(code=pm_code, defaults={'name': pm_name, 'kind': PaymentMethod.Kind.EWALLET, 'requires_reference': False})
                    Payment.objects.create(
                        order=order, method=pm, amount=order.total, reference_number=ref_num, received_by=order.cashier
                    )

                # 4. Create the Official Receipt Snapshot
                snapshot = {
                    "store": "FAKE ARAB SHAWARMA",
                    "items": [{"name": i.item_name, "quantity": i.quantity, "net": str(i.net)} for i in order.items.all()],
                    "subtotal": str(order.subtotal),
                    "discount_total": str(order.discount_total),
                    "total": str(order.total),
                    "tax_total": str(order.tax_total),
                    "payments": [{"method": pm.name, "amount": str(order.total)}],
                    "change": str(tendered - order.total) if pay_method == 'cash' else "0.00"
                }
                Receipt.objects.create(
                    order=order, receipt_number=f"RCT-{order.order_number}",
                    issued_by=order.cashier, snapshot=snapshot
                )

                return JsonResponse({"success": True, "order_number": order.order_number})
                
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    today = timezone.localdate()
    counter = OrderCounter.objects.filter(date=today).first()
    next_num = (counter.last_number + 1) if counter else 1
    next_order_number = f"{today:%y%m%d}-{next_num:04d}"

    categories = Category.objects.filter(is_active=True)
    variants = MenuVariant.objects.filter(is_active=True).select_related('item__category')
    
    return render(request, 'pos.html', {
        'categories': categories,
        'variants': variants,
        'next_order_number': next_order_number
    })

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

def order_list(request):
    orders = Order.objects.prefetch_related('items').order_by('-created_at')
    
    return render(request, 'order_list.html', {
        'orders': orders
    })

def transaction_history(request):
    # Sinama na natin ang pagkuha ng 'payments' sa database para bumilis ang loading
    orders = Order.objects.select_related('cashier').prefetch_related('items', 'payments__method').order_by('-created_at')
    
    total_revenue = Decimal('0.00')
    cash_total = Decimal('0.00')
    ewallet_total = Decimal('0.00')
    
    # I-compute ang bawat Payment method
    for o in orders:
        if o.status != Order.Status.CANCELLED:
            total_revenue += o.total
            for p in o.payments.all():
                if p.method.kind == 'cash':
                    cash_total += p.amount
                else:
                    ewallet_total += p.amount
                    
    return render(request, 'transaction_history.html', {
        'orders': orders,
        'total_revenue': total_revenue,
        'cash_total': cash_total,
        'ewallet_total': ewallet_total
    })

def receipts_list(request):
    receipts = Receipt.objects.select_related('order', 'issued_by').order_by('-issued_at')
    
    return render(request, 'receipts.html', {
        'receipts': receipts
    })