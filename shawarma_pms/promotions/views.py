import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.dateparse import parse_date, parse_time

from point_of_sale.models import Category, MenuVariant
from security.decorators import require_dynamic_access
from security.templatetags.access_tags import has_dynamic_access

from .models import AppliedDiscount, Promotion


def _clean_promotion(data):
    """Validate + normalise the 'add' payload. Raises ValueError with a user-friendly message."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Promotion name is required.")
    campaign, kind, scope = data.get("campaign_type"), data.get("kind"), data.get("scope", Promotion.Scope.ALL)
    if campaign not in Promotion.Campaign.values:
        raise ValueError("Invalid campaign type.")
    if kind not in Promotion.Kind.values:
        raise ValueError("Invalid discount kind.")
    if scope not in Promotion.Scope.values:
        raise ValueError("Invalid scope.")
    try:
        value = Decimal(str(data.get("value")))
    except (InvalidOperation, TypeError):
        raise ValueError("Discount value must be a number.")
    if value <= 0 or (kind in (Promotion.Kind.PERCENT, Promotion.Kind.BOGO) and value > 100):
        raise ValueError("Discount value must be above 0 (and at most 100 for percentages).")

    buy, get = int(data.get("buy_quantity") or 1), int(data.get("get_quantity") or 1)
    if kind == Promotion.Kind.BOGO and (buy < 1 or get < 1):
        raise ValueError("Buy and get quantities must be at least 1.")

    days = sorted({int(d) for d in data.get("days_of_week", []) if str(d) in "0123456" and str(d) != ""})
    start_time, end_time = parse_time(data.get("start_time") or ""), parse_time(data.get("end_time") or "")
    if bool(start_time) != bool(end_time):
        raise ValueError("Set both a start time and an end time, or leave both empty.")
    start_date, end_date = parse_date(data.get("start_date") or ""), parse_date(data.get("end_date") or "")
    if start_date and end_date and end_date < start_date:
        raise ValueError("End date cannot be before the start date.")

    target_ids = [int(t) for t in data.get("target_ids", []) if str(t).strip()]
    if scope != Promotion.Scope.ALL and not target_ids:
        raise ValueError("Select at least one category or product for this scope.")
    try:
        min_order = Decimal(str(data.get("min_order_amount") or 0))
    except InvalidOperation:
        raise ValueError("Minimum order amount must be a number.")
    # Manual campaigns are only ever applied by a cashier; everything else defaults to automatic.
    auto = bool(data.get("auto_apply", campaign != Promotion.Campaign.MANUAL)) and campaign != Promotion.Campaign.MANUAL

    return dict(name=name[:100], code=(data.get("code") or "")[:30], campaign_type=campaign, kind=kind, value=value,
                buy_quantity=buy, get_quantity=get, scope=scope, days_of_week=days, start_time=start_time,
                end_time=end_time, start_date=start_date, end_date=end_date, min_order_amount=min_order,
                auto_apply=auto, is_active=True), target_ids


@login_required
@require_dynamic_access("promotions.view_promotion")
def promotions_list(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get("action")

            if action == "add":
                if not has_dynamic_access(request.user, "promotions.add_promotion"):
                    return JsonResponse({"success": False, "error": "You do not have permission to create promotions."})
                fields, target_ids = _clean_promotion(data)
                with transaction.atomic():
                    promo = Promotion.objects.create(created_by=request.user, **fields)
                    if promo.scope == Promotion.Scope.CATEGORIES:
                        promo.categories.set(Category.objects.filter(pk__in=target_ids))
                    elif promo.scope == Promotion.Scope.VARIANTS:
                        promo.variants.set(MenuVariant.objects.filter(pk__in=target_ids))
                _refresh_recommendations()
                return JsonResponse({"success": True, "message": "Promotion created successfully!"})

            elif action == "toggle_status":
                if not has_dynamic_access(request.user, "promotions.change_promotion"):
                    return JsonResponse({"success": False, "error": "You do not have permission to change promotions."})
                promo = Promotion.objects.get(id=data["promo_id"])
                promo.is_active = not promo.is_active
                promo.save(update_fields=["is_active"])
                _refresh_recommendations()
                return JsonResponse({"success": True, "message": "Promotion status updated!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    promos = Promotion.objects.all().order_by("-created_at")
    categories = list(Category.objects.filter(is_active=True).values("id", "name"))
    variants = list(MenuVariant.objects.filter(is_active=True).values("id", "name", "item__name"))

    # Smart Promotion Recommendation -> open the form prefilled (the manager reviews/edits before saving)
    prefill, prefill_missing = None, False
    rec_id = request.GET.get("rec")
    if rec_id:
        from reports_analytics.recommendations import get_recommendation_by_id
        rec = get_recommendation_by_id(rec_id)
        prefill, prefill_missing = (rec["prefill"] if rec else None), rec is None

    return render(request, "promotions.html", {
        "promos": promos,
        "categories_json": json.dumps(categories),
        "variants_json": json.dumps(variants),
        "prefill_json": json.dumps(prefill),
        "prefill_missing": prefill_missing,
    })


def _refresh_recommendations():
    from reports_analytics.recommendations import invalidate_cache
    invalidate_cache()


@login_required
def promotion_history(request):
    history_logs = AppliedDiscount.objects.select_related("order", "promotion", "applied_by").order_by("-created_at")
    return render(request, "promotion_history.html", {"history_logs": history_logs})
