import json

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from point_of_sale.models import Order
from .services import record_payment


@login_required
@permission_required("billing_payment.add_payment", raise_exception=True)
@require_POST
def api_pay(request, order_id):
    """Body: {"method": "cash|gcash|maya|card", "amount": 250, "tendered": 300, "reference": "..."}"""
    order = get_object_or_404(Order, pk=order_id)
    data = json.loads(request.body or "{}")
    try:
        p = record_payment(order, data.get("method"), data.get("amount"), data.get("tendered"),
                           data.get("reference", ""), user=request.user)
    except (ValueError, ValidationError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    order.refresh_from_db()
    return JsonResponse({"payment_id": p.id, "change": str(p.change_given), "balance_due": str(order.balance_due),
                         "paid": order.is_paid})


@login_required
@permission_required("billing_payment.view_receipt", raise_exception=True)
@require_GET
def receipt_text(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if not hasattr(order, "receipt"):
        return JsonResponse({"error": "No receipt yet (order not fully paid)."}, status=404)
    order.receipt.print_count += 1
    order.receipt.save(update_fields=["print_count"])
    return HttpResponse(order.receipt.render_text(), content_type="text/plain; charset=utf-8")
