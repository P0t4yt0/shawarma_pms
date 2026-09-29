"""Aggregation, slow-period detection and demand forecasting. Idempotent - safe to re-run any time."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import ExtractHour
from django.utils import timezone

from billing_payment.models import Payment
from inventory.models import Ingredient, StockMovement
from point_of_sale.models import Order, OrderItem
from .models import (DailySalesSummary, DemandForecast, HourlySales, IngredientDailyUsage, PromotionSuggestion,
                     VariantDailySales)

Z = Decimal("0")


def _day_orders(day):
    return Order.objects.filter(created_at__date=day)          # respects TIME_ZONE when USE_TZ=True


# ------------------------------------------------------------------ 1. daily rollup
def build_daily_summary(day):
    orders = _day_orders(day)
    sold = orders.exclude(status=Order.Status.CANCELLED)
    agg = sold.aggregate(n=Count("id"), gross=Sum("subtotal"), disc=Sum("discount_total"),
                         net=Sum("total"), tax=Sum("tax_total"))
    n = agg["n"] or 0
    done = list(sold.filter(status=Order.Status.COMPLETED))
    tickets = [o.ticket_seconds for o in done if o.ticket_seconds is not None]
    preps = [o.prep_seconds for o in done if o.prep_seconds is not None]
    target = settings.KDS_TARGET_TICKET_SECONDS

    pay = (Payment.objects.filter(order__in=sold, status="completed").values("method__name")
           .annotate(t=Sum("amount")))
    summary, _ = DailySalesSummary.objects.update_or_create(date=day, defaults=dict(
        order_count=n, item_count=OrderItem.objects.filter(order__in=sold).aggregate(q=Sum("quantity"))["q"] or 0,
        gross_sales=agg["gross"] or Z, discount_total=agg["disc"] or Z, net_sales=agg["net"] or Z,
        tax_total=agg["tax"] or Z, cancelled_count=orders.filter(status=Order.Status.CANCELLED).count(),
        avg_order_value=((agg["net"] or Z) / n).quantize(Decimal("0.01")) if n else Z,
        avg_ticket_seconds=int(sum(tickets) / len(tickets)) if tickets else None,
        avg_prep_seconds=int(sum(preps) / len(preps)) if preps else None,
        late_ticket_count=sum(1 for t in tickets if t > target),
        payment_breakdown={r["method__name"]: str(r["t"]) for r in pay}))

    # hourly buckets (basis for slow-period detection)
    HourlySales.objects.filter(date=day).delete()
    HourlySales.objects.bulk_create([
        HourlySales(date=day, hour=r["h"], weekday=day.weekday(), order_count=r["n"], net_sales=r["s"] or Z)
        for r in sold.annotate(h=ExtractHour("created_at")).values("h").annotate(n=Count("id"), s=Sum("total"))])

    # per-variant sales (top sellers)
    _rebuild_variant_sales(day, sold)

    # ingredient usage (basis for forecasting)
    for ing in Ingredient.objects.filter(is_active=True):
        mv = StockMovement.objects.filter(ingredient=ing, created_at__date=day)
        def total(*types):
            return mv.filter(movement_type__in=types).aggregate(s=Sum("quantity_change"))["s"] or Z
        last = mv.order_by("-id").first()
        IngredientDailyUsage.objects.update_or_create(date=day, ingredient=ing, defaults=dict(
            quantity_used=-(total("sale", "sale_reversal")), quantity_wasted=-(total("waste", "spoilage")),
            ending_stock=last.balance_after if last else ing.current_stock))
    for f in DemandForecast.objects.filter(forecast_date=day):     # back-fill forecast accuracy
        u = IngredientDailyUsage.objects.filter(date=day, ingredient=f.ingredient).first()
        f.actual_usage = u.quantity_used if u else None
        f.save(update_fields=["actual_usage"])
    return summary


def _rebuild_variant_sales(day, sold_qs):
    """Exact per-line net (gross - discount) per variant; replaces the rough bulk_create above."""
    rows = defaultdict(lambda: [0, Z])
    for it in OrderItem.objects.filter(order__in=sold_qs):
        rows[it.variant_id][0] += it.quantity
        rows[it.variant_id][1] += it.net
    VariantDailySales.objects.filter(date=day).delete()
    VariantDailySales.objects.bulk_create([VariantDailySales(date=day, variant_id=v, quantity=q, net_sales=s)
                                           for v, (q, s) in rows.items()])


# ------------------------------------------------------------------ 2. reports
def top_variants(date_from, date_to, limit=5):
    return (VariantDailySales.objects.filter(date__range=(date_from, date_to))
            .values("variant__item__name", "variant__name")
            .annotate(units=Sum("quantity"), revenue=Sum("net_sales")).order_by("-units")[:limit])


# ------------------------------------------------------------------ 3. slow periods -> promo suggestions
def detect_slow_periods(lookback_weeks=8, threshold=None):
    """
    For each weekday: average orders per trading hour over the last N weeks. Hours below
    `threshold` x that weekday's mean are 'slow'; adjacent slow hours are merged into one window.
    """
    threshold = Decimal(str(threshold or getattr(settings, "SLOW_PERIOD_THRESHOLD", 0.6)))
    since = timezone.localdate() - timedelta(weeks=lookback_weeks)
    open_h, close_h = settings.STORE_OPEN_HOUR, settings.STORE_CLOSE_HOUR
    created = []
    for wd in range(7):
        days = list(DailySalesSummary.objects.filter(date__gte=since).values_list("date", flat=True))
        days = [d for d in days if d.weekday() == wd]
        if len(days) < 3:                       # not enough history for this weekday
            continue
        sums = defaultdict(int)
        for r in HourlySales.objects.filter(date__in=days):
            sums[r.hour] += r.order_count
        avg = {h: Decimal(sums[h]) / len(days) for h in range(open_h, close_h)}
        baseline = sum(avg.values()) / len(avg)
        if baseline == 0:
            continue
        slow = [h for h, v in avg.items() if v < baseline * threshold]
        # merge consecutive hours
        windows, cur = [], []
        for h in sorted(slow):
            if cur and h == cur[-1] + 1:
                cur.append(h)
            else:
                if cur: windows.append(cur)
                cur = [h]
        if cur: windows.append(cur)
        for w in windows:
            w_avg = sum(avg[h] for h in w) / len(w)
            gap = (1 - w_avg / baseline) * 100
            pct = 20 if gap >= 60 else 15 if gap >= 40 else 10
            if PromotionSuggestion.objects.filter(weekday=wd, start_hour=w[0], end_hour=w[-1] + 1,
                                                  status=PromotionSuggestion.Status.PENDING).exists():
                continue
            created.append(PromotionSuggestion.objects.create(
                weekday=wd, start_hour=w[0], end_hour=w[-1] + 1,
                avg_orders_per_hour=w_avg.quantize(Decimal("0.01")), baseline_orders_per_hour=baseline.quantize(Decimal("0.01")),
                gap_pct=gap.quantize(Decimal("0.1")), suggested_percent_off=pct,
                reason=f"Orders/hour are {gap:.0f}% below this weekday's average over the last {lookback_weeks} weeks."))
    return created


# ------------------------------------------------------------------ 4. perishables forecasting
def forecast_ingredient_demand(days_ahead=7, lookback_weeks=6):
    """Predicted usage = mean usage on the same weekday over the last N weeks (simple, explainable baseline)."""
    today = timezone.localdate()
    out = []
    for ing in Ingredient.objects.filter(is_active=True):
        history = defaultdict(list)
        for u in IngredientDailyUsage.objects.filter(ingredient=ing, date__gte=today - timedelta(weeks=lookback_weeks),
                                                     date__lt=today):
            history[u.date.weekday()].append(u.quantity_used)
        for i in range(1, days_ahead + 1):
            d = today + timedelta(days=i)
            vals = history.get(d.weekday())
            if not vals:
                continue
            pred = (sum(vals) / len(vals)).quantize(Decimal("0.001"))
            f, _ = DemandForecast.objects.update_or_create(
                ingredient=ing, forecast_date=d, method="same_weekday_avg",
                defaults=dict(predicted_usage=pred, lookback_weeks=lookback_weeks))
            out.append(f)
    return out


def suggest_purchase(ingredient, cover_days=None):
    """
    How much to buy so stock covers forecast demand for `cover_days` - capped at shelf life for perishables
    so we never buy more than can be sold before it spoils.
    """
    cover = cover_days or 3
    if ingredient.is_perishable and ingredient.shelf_life_days:
        cover = min(cover, ingredient.shelf_life_days)
    need = sum((f.predicted_usage for f in ingredient.forecasts.filter(
        forecast_date__gt=timezone.localdate(), forecast_date__lte=timezone.localdate() + timedelta(days=cover))), Z)
    return max(need - ingredient.current_stock, Z)


def spoilage_risk(ingredient):
    """Stock that exceeds forecast demand within the shelf life = likely to spoil."""
    if not (ingredient.is_perishable and ingredient.shelf_life_days):
        return Z
    horizon = timezone.localdate() + timedelta(days=ingredient.shelf_life_days)
    expected = sum((f.predicted_usage for f in ingredient.forecasts.filter(
        forecast_date__gt=timezone.localdate(), forecast_date__lte=horizon)), Z)
    return max(ingredient.current_stock - expected, Z)
