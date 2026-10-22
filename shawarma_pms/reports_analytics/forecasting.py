"""Read-only sales analytics that feed the Smart Promotion Recommendation banner.

Everything here is computed from the raw Order / OrderItem tables (the nightly roll-up tables are NOT
required, so the numbers are never stale). Rules that apply to every function:

  * Cancelled orders are ignored.
  * TODAY IS EXCLUDED. A half-finished day would make the afternoon look "slow" and skew every average.
  * Nothing is invented: if the data is not there the function returns an empty result.

Money is converted to float for the analysis maths (these are estimates, not accounting figures).
"""
from collections import Counter, defaultdict
from datetime import timedelta
from itertools import combinations
from statistics import mean, median

from django.conf import settings
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncDate
from django.db.models import Count
from django.utils import timezone

from inventory.models import Ingredient, LowStockAlert, RecipeItem, RecipeLine
from point_of_sale.models import MenuVariant, Order, OrderItem
from promotions.models import AppliedDiscount, Promotion

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_NET = ExpressionWrapper(F("quantity") * F("unit_price") - F("discount_amount"),
                         output_field=DecimalField(max_digits=14, decimal_places=2))


def cfg(name, default):
    """Read a tunable from settings.py with a safe default."""
    return getattr(settings, name, default)


# --------------------------------------------------------------------------- windows & raw queries
def window(days=None):
    """(since, today). The analysis window is [since, today) - today is excluded on purpose."""
    today = timezone.localdate()
    return today - timedelta(days=days or cfg("PROMO_REC_LOOKBACK_DAYS", 56)), today


def _sold(since, today):
    return (Order.objects.filter(created_at__date__gte=since, created_at__date__lt=today)
            .exclude(status=Order.Status.CANCELLED))


def daily_series(days=None):
    """[{date, orders, revenue}] for every day that had at least one sale."""
    since, today = window(days)
    rows = (_sold(since, today).annotate(d=TruncDate("created_at")).values("d")
            .annotate(orders=Count("id"), revenue=Sum("total")).order_by("d"))
    return [{"date": r["d"], "orders": r["orders"], "revenue": float(r["revenue"] or 0)} for r in rows]


def orders_by_day_hour(since=None, today=None):
    """({date: Counter(hour -> orders)}, {date: revenue}) in the store's local time."""
    if since is None:
        since, today = window()
    day_hours, day_rev = defaultdict(Counter), defaultdict(float)
    for created, total in _sold(since, today).values_list("created_at", "total").iterator():
        t = timezone.localtime(created)
        day_hours[t.date()][t.hour] += 1
        day_rev[t.date()] += float(total)
    return day_hours, day_rev


# --------------------------------------------------------------------------- 1. is there enough data?
def data_sufficiency():
    """Gate for the whole feature: enough trading days AND enough orders, otherwise no forecasts."""
    series = daily_series()
    days = len(series)
    orders = sum(r["orders"] for r in series)
    min_days, min_orders = cfg("PROMO_REC_MIN_DAYS", 14), cfg("PROMO_REC_MIN_ORDERS", 100)
    progress = int(min(1.0, days / min_days, orders / min_orders) * 100) if min_days and min_orders else 100
    return {"ok": days >= min_days and orders >= min_orders, "trading_days": days, "total_orders": orders,
            "min_days": min_days, "min_orders": min_orders, "progress_pct": progress}


def basic_insights():
    """Plain facts (no forecasts) that are safe to show even when data is thin."""
    series = daily_series()
    today = timezone.localdate()
    last7 = [r for r in series if r["date"] >= today - timedelta(days=7)]
    since, _ = window()
    best = (OrderItem.objects.filter(order__created_at__date__gte=since, order__created_at__date__lt=today)
            .exclude(order__status=Order.Status.CANCELLED)
            .values("variant__item__name", "variant__name").annotate(units=Sum("quantity"))
            .order_by("-units").first())
    best_name = None
    if best:
        best_name = best["variant__item__name"] + (f" ({best['variant__name']})" if best["variant__name"] else "")
    return {"orders_7d": sum(r["orders"] for r in last7),
            "revenue_7d": round(sum(r["revenue"] for r in last7), 2),
            "best_seller": best_name, "best_seller_units": best["units"] if best else 0}


# --------------------------------------------------------------------------- 2. daily / weekly / monthly trends
def period_trends():
    """Historical (observed) daily, weekly and monthly totals. Contains no projections."""
    series = daily_series(days=365)
    today = timezone.localdate()
    by_date = {r["date"]: r for r in series}

    daily = []
    for i in range(14, 0, -1):
        d = today - timedelta(days=i)
        r = by_date.get(d, {"orders": 0, "revenue": 0.0})
        daily.append({"date": d, "weekday": WEEKDAYS[d.weekday()], "orders": r["orders"], "revenue": r["revenue"]})

    weeks = defaultdict(lambda: {"orders": 0, "revenue": 0.0})
    for r in series:
        monday = r["date"] - timedelta(days=r["date"].weekday())
        if monday + timedelta(days=6) < today:                         # complete weeks only
            weeks[monday]["orders"] += r["orders"]
            weeks[monday]["revenue"] += r["revenue"]
    weekly, prev = [], None
    for monday in sorted(weeks)[-8:]:
        w = weeks[monday]
        change = ((w["revenue"] - prev) / prev * 100) if prev else None
        weekly.append({"week_start": monday, "orders": w["orders"], "revenue": w["revenue"], "change_pct": change})
        prev = w["revenue"] or None

    months = defaultdict(lambda: {"orders": 0, "revenue": 0.0})
    for r in series:
        months[(r["date"].year, r["date"].month)]["orders"] += r["orders"]
        months[(r["date"].year, r["date"].month)]["revenue"] += r["revenue"]
    monthly = [{"label": f"{y}-{m:02d}", "orders": v["orders"], "revenue": v["revenue"],
                "partial": (y, m) == (today.year, today.month)} for (y, m), v in sorted(months.items())[-6:]]

    wd = defaultdict(list)
    since, _ = window()
    for r in series:
        if r["date"] >= since:
            wd[r["date"].weekday()].append(r)
    weekday_profile = [{"weekday": WEEKDAYS[i], "days": len(wd[i]),
                        "avg_orders": mean(x["orders"] for x in wd[i]) if wd[i] else None,
                        "avg_revenue": mean(x["revenue"] for x in wd[i]) if wd[i] else None} for i in range(7)]
    return {"daily": daily, "weekly": weekly, "monthly": monthly, "weekday_profile": weekday_profile}


def forecast_next_7_days():
    """Next 7 days = average revenue on the same weekday over the window (needs >= 2 samples per weekday)."""
    since, today = window()
    by_wd = defaultdict(list)
    for r in daily_series():
        if r["date"] >= since:
            by_wd[r["date"].weekday()].append(r)
    out, total = [], 0.0
    for i in range(0, 7):
        d = today + timedelta(days=i)
        rows = by_wd.get(d.weekday(), [])
        if len(rows) < 2:
            out.append({"date": d, "weekday": WEEKDAYS[d.weekday()], "orders": None, "revenue": None, "samples": len(rows)})
            continue
        rev, orders = mean(r["revenue"] for r in rows), mean(r["orders"] for r in rows)
        total += rev
        out.append({"date": d, "weekday": WEEKDAYS[d.weekday()], "orders": orders, "revenue": rev, "samples": len(rows)})
    return {"days": out, "total_revenue": total, "complete": all(x["revenue"] is not None for x in out),
            "method": "Average revenue of the same weekday over the last "
                      f"{cfg('PROMO_REC_LOOKBACK_DAYS', 56) // 7} weeks"}


# --------------------------------------------------------------------------- 3. peak and low-sales periods
def hourly_profile(day_hours=None, day_rev=None):
    """{weekday: {days, hours: {hour: {orders, revenue}}, aov}} - averages PER TRADING DAY of that weekday."""
    if day_hours is None:
        day_hours, day_rev = orders_by_day_hour()
    open_h, close_h = cfg("STORE_OPEN_HOUR", 10), cfg("STORE_CLOSE_HOUR", 22)
    by_wd = defaultdict(list)
    for d in day_hours:
        by_wd[d.weekday()].append(d)
    profile = {}
    for wd, dates in by_wd.items():
        n = len(dates)
        total_orders = sum(sum(day_hours[d].values()) for d in dates)
        profile[wd] = {
            "days": n,
            "aov": (sum(day_rev[d] for d in dates) / total_orders) if total_orders else 0.0,
            "hours": {h: {"orders": sum(day_hours[d][h] for d in dates) / n} for h in range(open_h, close_h)},
        }
    return profile


def slow_windows(profile):
    """Runs of consecutive hours well below that weekday's average (default < 60 %)."""
    threshold = float(cfg("SLOW_PERIOD_THRESHOLD", 0.6))
    min_days = cfg("PROMO_REC_MIN_WEEKDAY_SAMPLES", 3)
    min_base = float(cfg("PROMO_REC_MIN_BASELINE_ORDERS_PER_HOUR", 1.0))
    out = []
    for wd in sorted(profile):
        info = profile[wd]
        if info["days"] < min_days or not info["hours"]:
            continue
        baseline = mean(v["orders"] for v in info["hours"].values())
        if baseline < min_base:
            continue
        slow = sorted(h for h, v in info["hours"].items() if v["orders"] < baseline * threshold)
        runs, cur = [], []
        for h in slow:
            if cur and h == cur[-1] + 1:
                cur.append(h)
            else:
                if cur:
                    runs.append(cur)
                cur = [h]
        if cur:
            runs.append(cur)
        for run in runs:
            w_avg = mean(info["hours"][h]["orders"] for h in run)
            out.append({"weekday": wd, "start_hour": run[0], "end_hour": run[-1] + 1, "window_avg": w_avg,
                        "baseline": baseline, "gap_pct": (1 - w_avg / baseline) * 100,
                        "days_sampled": info["days"], "aov": info["aov"]})
    return out


def heatmap(profile):
    """Weekday x hour grid of average orders, with a 0-4 intensity level for colouring."""
    open_h, close_h = cfg("STORE_OPEN_HOUR", 10), cfg("STORE_CLOSE_HOUR", 22)
    peak = max((v["orders"] for p in profile.values() for v in p["hours"].values()), default=0) or 1
    rows = []
    for wd in range(7):
        info = profile.get(wd)
        cells = []
        for h in range(open_h, close_h):
            v = info["hours"][h]["orders"] if info else None
            cells.append({"orders": v, "level": None if v is None else min(4, int(v / peak * 4.999))})
        rows.append({"weekday": WEEKDAYS[wd], "days": info["days"] if info else 0, "cells": cells})
    return {"hours": [f"{(h % 12) or 12}{'a' if h < 12 else 'p'}" for h in range(open_h, close_h)], "rows": rows}


# --------------------------------------------------------------------------- 4. product performance
def variant_sales():
    """{variant_id: {daily: {date: units}, units, revenue}} over the window."""
    since, today = window()
    rows = (OrderItem.objects.filter(order__created_at__date__gte=since, order__created_at__date__lt=today)
            .exclude(order__status=Order.Status.CANCELLED).annotate(d=TruncDate("order__created_at"))
            .values("variant_id", "d").annotate(units=Sum("quantity"), revenue=Sum(_NET)))
    out = defaultdict(lambda: {"daily": {}, "units": 0, "revenue": 0.0})
    for r in rows:
        s = out[r["variant_id"]]
        s["daily"][r["d"]] = r["units"]
        s["units"] += r["units"]
        s["revenue"] += float(r["revenue"] or 0)
    return out


def product_performance(trading_days):
    """Best sellers, slow movers and declining products (recent 14 days vs the 14 before)."""
    _, today = window()
    n = cfg("PROMO_REC_TREND_DAYS", 14)
    min_units = cfg("PROMO_REC_MIN_PRIOR_UNITS", 10)
    decline = float(cfg("PROMO_REC_DECLINE_PCT", 25))
    slow_freq = float(cfg("PROMO_REC_SLOW_FREQ_PCT", 30))
    sales = variant_sales()
    variants = {v.id: v for v in MenuVariant.objects.filter(is_active=True, item__is_active=True).select_related("item")}
    rows = []
    for vid, v in variants.items():
        s = sales.get(vid)
        if not s:
            continue
        recent = sum(u for d, u in s["daily"].items() if d >= today - timedelta(days=n))
        prior = sum(u for d, u in s["daily"].items() if today - timedelta(days=2 * n) <= d < today - timedelta(days=n))
        weekly_units = [0, 0, 0, 0]
        for d, u in s["daily"].items():
            idx = (today - d).days // 7
            if idx < 4:
                weekly_units[idx] += u
        rows.append({
            "variant_id": vid, "item_id": v.item_id, "name": f"{v.item.name} ({v.name})" if v.name else v.item.name,
            "price": float(v.price), "units": s["units"], "revenue": s["revenue"],
            "days_sold": len(s["daily"]), "freq_pct": len(s["daily"]) / trading_days * 100 if trading_days else 0,
            "recent_per_day": recent / n, "prior_per_day": prior / n,
            "change_pct": ((recent - prior) / prior * 100) if prior >= min_units else None,
            "weekly_units": list(reversed(weekly_units)),          # oldest -> newest
        })
    best = sorted(rows, key=lambda r: -r["units"])[:5]
    best_ids = {r["variant_id"] for r in best}
    declining = sorted((r for r in rows if r["change_pct"] is not None and r["change_pct"] <= -decline),
                       key=lambda r: r["change_pct"])
    slow = sorted((r for r in rows if r["freq_pct"] <= slow_freq and r["variant_id"] not in best_ids),
                  key=lambda r: r["units"])[:5]
    return {"all": rows, "best": best, "slow": slow, "declining": declining}


# --------------------------------------------------------------------------- 5. product combinations
def product_pairs():
    """Variants that appear in the same order. Same-item pairs (e.g. Standard + Large) are ignored."""
    since, today = window()
    baskets = defaultdict(set)
    for oid, vid in (OrderItem.objects.filter(order__created_at__date__gte=since, order__created_at__date__lt=today)
                     .exclude(order__status=Order.Status.CANCELLED).values_list("order_id", "variant_id")):
        baskets[oid].add(vid)
    n_orders = len(baskets)
    if not n_orders:
        return []
    single, pair = Counter(), Counter()
    for vs in baskets.values():
        single.update(vs)
        pair.update(combinations(sorted(vs), 2))
    meta = {v.id: v for v in MenuVariant.objects.filter(is_active=True, item__is_active=True).select_related("item")}
    min_pair, min_lift = cfg("PROMO_REC_MIN_PAIR_ORDERS", 8), float(cfg("PROMO_REC_MIN_LIFT", 1.2))
    out = []
    for (a, b), c in pair.items():
        if c < min_pair or a not in meta or b not in meta or meta[a].item_id == meta[b].item_id:
            continue
        lift = (c / n_orders) / ((single[a] / n_orders) * (single[b] / n_orders))
        if lift < min_lift:
            continue
        name = lambda v: f"{v.item.name} ({v.name})" if v.name else v.item.name
        out.append({"a": a, "b": b, "a_name": name(meta[a]), "b_name": name(meta[b]),
                    "a_price": float(meta[a].price), "b_price": float(meta[b].price),
                    "pair_orders": c, "support_pct": c / n_orders * 100, "lift": lift,
                    "pct_of_a": c / single[a] * 100, "pct_of_b": c / single[b] * 100})
    return sorted(out, key=lambda r: -r["pair_orders"])[:10]


# --------------------------------------------------------------------------- 6. cost, margin and stock
def load_recipes():
    """{variant_id: [(ingredient_id, qty)]}. Recipe Management (RecipeItem) wins; RecipeLine is the fallback."""
    recipes = defaultdict(list)
    for vid, ing, qty in RecipeItem.objects.values_list("variant_id", "ingredient_id", "quantity_required"):
        recipes[vid].append((ing, float(qty)))
    covered = set(recipes)
    for vid, ing, qty in (RecipeLine.objects.filter(recipe__is_active=True)
                          .values_list("recipe__variant_id", "ingredient_id", "quantity")):
        if vid not in covered:
            recipes[vid].append((ing, float(qty)))
    return dict(recipes)


def load_ingredients():
    return {i.id: {"name": i.name, "unit": i.unit, "stock": float(i.current_stock), "cost": float(i.cost_per_unit),
                   "reorder": float(i.reorder_level)} for i in Ingredient.objects.all()}


def variant_costs(recipes, ingredients):
    """{variant_id: unit cost} - only where a recipe exists AND its cost is > 0 (otherwise margin is unknown)."""
    out = {}
    for vid, lines in recipes.items():
        cost = sum(q * ingredients[i]["cost"] for i, q in lines if i in ingredients)
        if cost > 0:
            out[vid] = cost
    return out


def low_stock_ingredient_ids():
    return set(LowStockAlert.objects.exclude(status=LowStockAlert.Status.RESOLVED).values_list("ingredient_id", flat=True))


# --------------------------------------------------------------------------- 7. promotion history
def _promo_hours(p):
    if p.start_time and p.end_time:
        return list(range(p.start_time.hour, p.end_time.hour + 1))
    return None


def promotion_history(day_hours=None):
    """Observed performance of past promotions: orders on days/hours a promo was used vs. the same weekdays without it."""
    since, today = window()
    if day_hours is None:
        day_hours, _ = orders_by_day_hour(since, today)
    trading = set(day_hours)
    per = defaultdict(lambda: {"dates": set(), "discount": 0.0, "orders": set()})
    qs = (AppliedDiscount.objects.filter(created_at__date__gte=since, created_at__date__lt=today)
          .annotate(d=TruncDate("created_at")).values_list("promotion_id", "d", "order_id", "amount"))
    for pid, d, oid, amount in qs:
        per[pid]["dates"].add(d)
        per[pid]["orders"].add(oid)
        per[pid]["discount"] += float(amount)
    promos = Promotion.objects.in_bulk(list(per))
    out = []
    for pid, info in per.items():
        p = promos.get(pid)
        if not p:
            continue
        hours = _promo_hours(p)
        count = (lambda d: sum(day_hours[d][h] for h in hours)) if hours else (lambda d: sum(day_hours[d].values()))
        promo_days = info["dates"] & trading
        wds = {d.weekday() for d in promo_days}
        base_days = [d for d in trading if d not in promo_days and d.weekday() in wds]
        measurable = p.campaign_type != Promotion.Campaign.MANUAL        # cashier-applied discounts don't drive traffic
        promo_avg = mean(count(d) for d in promo_days) if promo_days else None
        base_avg = mean(count(d) for d in base_days) if base_days else None
        reliable = bool(measurable and len(promo_days) >= 3 and len(base_days) >= 3 and base_avg)
        label = (f"{p.value:g}% off" if p.kind == Promotion.Kind.PERCENT else
                 f"₱{p.value:g} off" if p.kind == Promotion.Kind.FIXED else f"Buy {p.buy_quantity} get {p.get_quantity}")
        out.append({
            "promotion_id": pid, "name": p.name, "campaign": p.get_campaign_type_display(), "discount_label": label,
            "percent": float(p.value) if p.kind == Promotion.Kind.PERCENT else None,
            "orders_with_promo": len(info["orders"]), "total_discount": info["discount"],
            "days_used": len(promo_days), "avg_orders_promo": promo_avg, "avg_orders_baseline": base_avg,
            "lift_pct": ((promo_avg / base_avg - 1) * 100) if reliable else None, "reliable": reliable,
            "measured_on": "promo hours" if hours else "whole day",
            "variant_ids": list(p.variants.values_list("id", flat=True)) if p.scope == Promotion.Scope.VARIANTS else [],
            "weekdays": list(p.days_of_week or []),
            "start_hour": p.start_time.hour if p.start_time else None,
            "end_hour": (p.end_time.hour + 1) if p.end_time else None,
        })
    return sorted(out, key=lambda r: -r["orders_with_promo"])


def observed_elasticity(history):
    """Volume lift (%) per 1 % of discount, taken from past promotions that can be measured. None if no evidence."""
    vals = [h["lift_pct"] / h["percent"] for h in history if h["reliable"] and h["percent"] and h["lift_pct"] is not None]
    if not vals:
        return None
    return max(0.3, min(3.0, median(vals)))
