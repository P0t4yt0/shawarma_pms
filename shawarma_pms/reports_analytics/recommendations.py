"""Smart Promotion Recommendations.

Turns the observed signals from `forecasting.py` into a short, ranked list of promotions. Every recommendation:

  * is derived from the store's own sales history (nothing is random or fixed),
  * passes the PROFIT guardrail  (margin after discount stays above PROMO_REC_MIN_MARGIN_PCT),
  * passes the STOCK guardrail   (ingredients cover the expected extra demand; no open low-stock alert),
  * carries a plain-language explanation, the evidence behind it, and a prefill for the promotion form.

If there is not enough history the result contains ONLY the fallback message and plain historical facts -
never a forecast. Anything a promotion would cost or earn is an ESTIMATE and is labelled that way.

Public API
    get_cached_recommendations(force=False) -> dict        (used by the dashboard banner and the report page)
    get_recommendation_by_id(rec_id)        -> dict | None (used by the Create Promotion prefill)
    invalidate_cache()
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from promotions.models import Promotion

from . import forecasting as fc
from .forecasting import cfg

log = logging.getLogger(__name__)

FALLBACK_MESSAGE = ("Not enough sales data yet to generate reliable promotion recommendations. "
                    "Keep recording sales to unlock personalized suggestions.")
CACHE_KEY = "promo_recommendations_v1"
MAX_RECOMMENDATIONS = 6


# --------------------------------------------------------------------------- formatting helpers
def _peso(x):
    return f"₱{x:,.0f}"


def _t(h):
    h %= 24
    return f"{(h % 12) or 12}:00 {'AM' if h < 12 else 'PM'}"


def _range(s, e):
    return f"{_t(s)}–{_t(e)}"


def _margin_after(price, cost, d):
    p = price * (1 - d / 100)
    return (p - cost) / p * 100 if p > 0 else 0.0


# --------------------------------------------------------------------------- guardrails
def _max_discount_pct(cost_ratio):
    """Largest discount that keeps margin >= the floor. cost_ratio = unit cost / unit price."""
    floor = float(cfg("PROMO_REC_MIN_MARGIN_PCT", 20)) / 100
    return max(0.0, (1 - cost_ratio / (1 - floor)) * 100)


def _best_discount(existing, price, cost, extra_fn, cap_pct):
    """Pick the discount step with the highest estimated weekly NET profit gain (must be > 0).

    existing  volume that would get the discount anyway (pure give-away)
    extra_fn  d% -> estimated extra volume won by the discount
    net       = extra * (discounted price - cost)  -  existing * price * d%
    """
    best = None
    for d in cfg("PROMO_REC_DISCOUNT_STEPS", (5, 10, 15, 20)):
        if d > cap_pct:
            continue
        extra = extra_fn(d)
        net = extra * (price * (1 - d / 100) - cost) - existing * price * d / 100
        if net > 0 and (best is None or net > best["net"]):
            best = {"d": d, "extra": extra, "net": net, "margin_after": _margin_after(price, cost, d)}
    return best


def _stock_check(ctx, extra_units, base_units):
    """Can current stock cover promo-day demand? Returns (ok, reason)."""
    need = defaultdict(float)
    for vid in set(extra_units) | set(base_units):
        for ing_id, qty in ctx["recipes"].get(vid, []):
            need[ing_id] += (base_units.get(vid, 0.0) + extra_units.get(vid, 0.0)) * qty
    cover, buffer = float(cfg("PROMO_REC_STOCK_COVER_DAYS", 2)), float(cfg("PROMO_REC_STOCK_BUFFER", 1.2))
    for ing_id, per_day in need.items():
        ing = ctx["ingredients"].get(ing_id)
        if not ing:
            continue
        if ing_id in ctx["low_ids"]:
            return False, f"{ing['name']} is already low on stock"
        required = per_day * cover * buffer
        if ing["stock"] < required:
            return False, (f"not enough {ing['name']} for the extra demand "
                           f"(have {ing['stock']:g} {ing['unit']}, need about {required:g} {ing['unit']})")
    return True, None


# --------------------------------------------------------------------------- overlap / history checks
def _active_promos():
    today = timezone.localdate()
    out = []
    for p in Promotion.objects.filter(is_active=True).prefetch_related("variants", "categories"):
        if p.end_date and p.end_date < today:
            continue
        if p.usage_limit is not None and p.times_used >= p.usage_limit:
            continue
        if p.campaign_type == Promotion.Campaign.MANUAL:           # cashier-applied: not a campaign
            continue
        out.append(p)
    return out


def _slow_overlaps(promos, wd, s, e):
    for p in promos:
        if p.days_of_week and wd not in p.days_of_week:
            continue
        if p.start_time and p.end_time:
            if p.start_time.hour < e and p.end_time.hour + 1 > s:
                return True
        elif p.scope == Promotion.Scope.ALL:                          # whole menu, all day: already discounted
            return True
    return False


def _variant_promoted(promos, vid, category_id):
    for p in promos:
        if p.scope == Promotion.Scope.VARIANTS and any(v.id == vid for v in p.variants.all()):
            return True
        if p.scope == Promotion.Scope.CATEGORIES and any(c.id == category_id for c in p.categories.all()):
            return True
        if p.scope == Promotion.Scope.ALL and not p.start_time and not p.days_of_week:
            return True
    return False


def _failed_before_slow(history, wd, s, e):
    for h in history:
        if (h["reliable"] and h["lift_pct"] is not None and h["lift_pct"] <= 0 and h["start_hour"] is not None
                and (not h["weekdays"] or wd in h["weekdays"]) and h["start_hour"] < e and h["end_hour"] > s):
            return h
    return None


def _failed_before_variants(history, vids):
    for h in history:
        if h["reliable"] and h["lift_pct"] is not None and h["lift_pct"] <= 0 and set(h["variant_ids"]) & set(vids):
            return h
    return None


# --------------------------------------------------------------------------- context
def _build_context(history, perf_all):
    recipes, ingredients = fc.load_recipes(), fc.load_ingredients()
    costs = fc.variant_costs(recipes, ingredients)
    observed = fc.observed_elasticity(history)
    total = known = weighted = 0.0
    for r in perf_all:
        total += r["revenue"]
        c = costs.get(r["variant_id"])
        if c and r["price"] > 0:
            known += r["revenue"]
            weighted += r["revenue"] * (c / r["price"])
    coverage = known / total if total else 0.0
    from point_of_sale.models import MenuVariant
    return {
        "recipes": recipes, "ingredients": ingredients, "costs": costs, "low_ids": fc.low_stock_ingredient_ids(),
        "elasticity": observed or float(cfg("PROMO_REC_DEFAULT_ELASTICITY", 2.0)),
        "elasticity_source": "observed" if observed else "assumed",
        "menu_cost_ratio": (weighted / known) if known and coverage >= float(cfg("PROMO_REC_MIN_COST_COVERAGE", 0.6)) else None,
        "cost_coverage": coverage,
        "category_of": dict(MenuVariant.objects.values_list("id", "item__category_id")),
        "active_promos": _active_promos(),
    }


def _source_note(ctx):
    return ("measured from your past promotions" if ctx["elasticity_source"] == "observed"
            else "assumed, because no past promotion could be measured yet")


def _confidence(points):
    return "High" if points >= 3 else "Medium" if points == 2 else "Low"


def _links(rec_id):
    return {"report_url": f"{reverse('reports_analytics:sales_forecast_report')}?rec={rec_id}",
            "create_url": f"{reverse('promotions:list')}?rec={rec_id}"}


# --------------------------------------------------------------------------- recommendation type 1: slow periods
def _slow_period_recs(ctx, windows, history, perf_all, trading_days, total_orders):
    recs, skipped = [], []
    today = timezone.localdate()
    base_units = {r["variant_id"]: r["units"] / trading_days for r in perf_all}
    gap_share = float(cfg("PROMO_REC_GAP_RECOVERY_PER_PCT", 0.02))
    max_recovery = float(cfg("PROMO_REC_MAX_GAP_RECOVERY", 0.5))
    for w in windows:
        wd, s, e = w["weekday"], w["start_hour"], w["end_hour"]
        day = fc.WEEKDAYS_LONG[wd]
        label = f"{day}s {_range(s, e)}"
        rid = f"slow-{wd}-{s}-{e}"
        if _slow_overlaps(ctx["active_promos"], wd, s, e):
            skipped.append(f"{label}: an active promotion already covers this period.")
            continue
        failed = _failed_before_slow(history, wd, s, e)
        if failed:
            skipped.append(f"{label}: a previous promotion here (“{failed['name']}”) did not lift sales.")
            continue
        ratio = ctx["menu_cost_ratio"]
        if ratio is None:
            skipped.append(f"{label}: profit margins are unknown for most products. Add recipes with ingredient costs.")
            continue
        hours = e - s
        existing = w["window_avg"] * hours
        gap_orders = (w["baseline"] - w["window_avg"]) * hours
        aov = w["aov"]
        best = _best_discount(existing, aov, aov * ratio,
                              lambda d: gap_orders * min(max_recovery, gap_share * d), _max_discount_pct(ratio))
        if not best:
            skipped.append(f"{label}: no discount level is profitable while keeping margin above "
                           f"{cfg('PROMO_REC_MIN_MARGIN_PCT', 20)}%.")
            continue
        extra_units = {vid: best["extra"] * (r_units / total_orders) for vid, r_units in
                       ((r["variant_id"], r["units"]) for r in perf_all)}
        ok, why = _stock_check(ctx, extra_units, base_units)
        if not ok:
            skipped.append(f"{label}: {why}.")
            continue
        d = best["d"]
        share = min(max_recovery, gap_share * d) * 100
        points = (w["days_sampled"] >= 6) + (w["gap_pct"] >= 50)
        recs.append({
            "id": rid, "type": "slow_period", "badge": "Slow period", "icon": "ph-clock-countdown",
            "title": f"Boost {day} {_range(s, e)}",
            "headline": f"Run a {d}% Happy Hour on {day}s, {_range(s, e)}",
            "explanation": (f"{day}s {_range(s, e)} average {w['window_avg']:.1f} orders/hour versus {w['baseline']:.1f} "
                            f"across the day ({w['gap_pct']:.0f}% lower, from {w['days_sampled']} {day}s). "
                            f"A {d}% deal can win orders while the kitchen is quiet."),
            "estimate": f"≈ +{_peso(best['net'])}/week profit (estimate)",
            "estimate_basis": f"Assumes a {d}% discount wins back about {share:.0f}% of the missing orders in this window.",
            "confidence": _confidence(points), "score": best["net"],
            "guardrails": [f"Margin after discount ≈ {best['margin_after']:.0f}% (minimum {cfg('PROMO_REC_MIN_MARGIN_PCT', 20)}%)",
                           "Ingredient stock covers the expected extra orders"],
            "evidence": {
                "metrics": [("Average orders/hour in window", f"{w['window_avg']:.1f}"),
                            (f"Average orders/hour, whole {day}", f"{w['baseline']:.1f}"),
                            ("Gap vs the day's average", f"{w['gap_pct']:.0f}%"),
                            (f"{day}s analysed", str(w["days_sampled"])),
                            ("Average order value", _peso(aov)),
                            ("Estimated extra orders per week", f"{best['extra']:.1f}"),
                            ("Estimated weekly profit change", _peso(best["net"]))],
                "hours_weekday": wd, "window": [s, e],
            },
            "prefill": {"rec_id": rid, "name": f"{day} slow-hour deal ({_range(s, e)})", "campaign_type": "happy_hour",
                        "kind": "percent", "value": d, "buy_quantity": 1, "get_quantity": 1, "scope": "all",
                        "target_ids": [], "days_of_week": [wd], "start_time": f"{s:02d}:00", "end_time": f"{e - 1:02d}:59",
                        "start_date": today.isoformat(), "end_date": (today + timedelta(days=28)).isoformat(),
                        "min_order_amount": 0, "auto_apply": True},
            **_links(rid),
        })
    return recs, skipped


# --------------------------------------------------------------------------- recommendation type 2: declining products
def _declining_recs(ctx, declining, history, trading_days):
    recs, skipped = [], []
    today = timezone.localdate()
    for r in declining:
        vid, name = r["variant_id"], r["name"]
        cost = ctx["costs"].get(vid)
        if cost is None or r["price"] <= 0:
            skipped.append(f"{name}: sales are falling, but its cost is unknown. Add a recipe to evaluate a promotion.")
            continue
        if _variant_promoted(ctx["active_promos"], vid, ctx["category_of"].get(vid)):
            skipped.append(f"{name}: already covered by an active promotion.")
            continue
        failed = _failed_before_variants(history, [vid])
        if failed:
            skipped.append(f"{name}: a previous promotion (“{failed['name']}”) did not lift its sales.")
            continue
        recent_week, lost_week = r["recent_per_day"] * 7, (r["prior_per_day"] - r["recent_per_day"]) * 7
        best = _best_discount(recent_week, r["price"], cost,
                              lambda d: min(lost_week, recent_week * ctx["elasticity"] * d / 100),
                              _max_discount_pct(cost / r["price"]))
        if not best:
            skipped.append(f"{name}: no discount level is profitable while keeping margin above "
                           f"{cfg('PROMO_REC_MIN_MARGIN_PCT', 20)}%.")
            continue
        ok, why = _stock_check(ctx, {vid: best["extra"] / 7}, {vid: r["recent_per_day"]})
        if not ok:
            skipped.append(f"{name}: {why}.")
            continue
        d, rid = best["d"], f"decl-{vid}"
        points = (r["units"] >= 40) + (ctx["elasticity_source"] == "observed") + (r["change_pct"] <= -40)
        recs.append({
            "id": rid, "type": "declining_product", "badge": "Declining product", "icon": "ph-trend-down",
            "title": f"Win back {name}",
            "headline": f"Offer {d}% off {name} for 2 weeks",
            "explanation": (f"{name} sold {r['recent_per_day']:.1f}/day over the last 14 days, down {abs(r['change_pct']):.0f}% "
                            f"from {r['prior_per_day']:.1f}/day before that. A short {d}% deal can win those customers back."),
            "estimate": f"≈ +{_peso(best['net'])}/week profit (estimate)",
            "estimate_basis": (f"Assumes each 1% of discount lifts units by about {ctx['elasticity']:.1f}% "
                               f"({_source_note(ctx)}), capped at the previous sales level."),
            "confidence": _confidence(points), "score": best["net"],
            "guardrails": [f"Margin after discount ≈ {best['margin_after']:.0f}% (minimum {cfg('PROMO_REC_MIN_MARGIN_PCT', 20)}%)",
                           "Ingredient stock covers the expected extra units"],
            "evidence": {"metrics": [("Units/day, last 14 days", f"{r['recent_per_day']:.1f}"),
                                     ("Units/day, previous 14 days", f"{r['prior_per_day']:.1f}"),
                                     ("Change", f"{r['change_pct']:.0f}%"),
                                     ("Price / unit cost", f"{_peso(r['price'])} / {_peso(cost)}"),
                                     ("Estimated extra units per week", f"{best['extra']:.1f}"),
                                     ("Estimated weekly profit change", _peso(best["net"]))],
                         "weekly_units": r["weekly_units"], "variant_name": name},
            "prefill": {"rec_id": rid, "name": f"{name} comeback deal"[:100], "campaign_type": "slow_period",
                        "kind": "percent", "value": d, "buy_quantity": 1, "get_quantity": 1, "scope": "variants",
                        "target_ids": [vid], "days_of_week": [], "start_time": "", "end_time": "",
                        "start_date": today.isoformat(), "end_date": (today + timedelta(days=13)).isoformat(),
                        "min_order_amount": 0, "auto_apply": True},
            **_links(rid),
        })
    return recs, skipped


# --------------------------------------------------------------------------- recommendation type 3: bundles
def _bundle_recs(ctx, pairs, history, perf_all, trading_days, lookback_days):
    recs, skipped = [], []
    today = timezone.localdate()
    per_day = {r["variant_id"]: r["units"] / trading_days for r in perf_all}
    weeks = max(lookback_days / 7, 1)
    for p in pairs[:3]:
        a, b = p["a"], p["b"]
        title = f"{p['a_name']} + {p['b_name']}"
        ca, cb = ctx["costs"].get(a), ctx["costs"].get(b)
        if ca is None or cb is None:
            skipped.append(f"Bundle {title}: frequently ordered together, but a cost is unknown. Add recipes to evaluate it.")
            continue
        if any(_variant_promoted(ctx["active_promos"], v, ctx["category_of"].get(v)) for v in (a, b)):
            skipped.append(f"Bundle {title}: one of the items is already in an active promotion.")
            continue
        failed = _failed_before_variants(history, [a, b])
        if failed:
            skipped.append(f"Bundle {title}: a previous promotion (“{failed['name']}”) did not lift sales.")
            continue
        price, cost = p["a_price"] + p["b_price"], ca + cb
        weekly_pairs = p["pair_orders"] / weeks
        best = _best_discount(weekly_pairs, price, cost,
                              lambda d: min(weekly_pairs * 0.5, weekly_pairs * ctx["elasticity"] * d / 100),
                              _max_discount_pct(cost / price))
        if not best:
            skipped.append(f"Bundle {title}: no discount level is profitable while keeping margin above "
                           f"{cfg('PROMO_REC_MIN_MARGIN_PCT', 20)}%.")
            continue
        extra_day = best["extra"] / 7
        ok, why = _stock_check(ctx, {a: extra_day, b: extra_day}, {a: per_day.get(a, 0), b: per_day.get(b, 0)})
        if not ok:
            skipped.append(f"Bundle {title}: {why}.")
            continue
        d, rid = best["d"], f"bundle-{a}-{b}"
        points = (p["pair_orders"] >= 20) + (p["lift"] >= 1.5) + (ctx["elasticity_source"] == "observed")
        recs.append({
            "id": rid, "type": "bundle", "badge": "Bundle", "icon": "ph-stack",
            "title": f"Bundle {title}",
            "headline": f"Offer {d}% off when {p['a_name']} and {p['b_name']} are bought together",
            "explanation": (f"{p['a_name']} and {p['b_name']} were ordered together in {p['pair_orders']} orders "
                            f"({p['pct_of_a']:.0f}% of {p['a_name']} orders, {p['lift']:.1f}× more often than chance). "
                            f"A {d}% bundle price can lift these pairings."),
            "estimate": f"≈ +{_peso(best['net'])}/week profit (estimate)",
            "estimate_basis": (f"Assumes each 1% of discount adds about {ctx['elasticity']:.1f}% more pairings "
                               f"({_source_note(ctx)}), capped at +50%."),
            "confidence": _confidence(points), "score": best["net"] * 0.9,      # slight penalty: discount can leak to solo buys
            "guardrails": [f"Margin after discount ≈ {best['margin_after']:.0f}% (minimum {cfg('PROMO_REC_MIN_MARGIN_PCT', 20)}%)",
                           "Ingredient stock covers the expected extra units",
                           "Applies only when the order total reaches the combined price of both items"],
            "evidence": {"metrics": [("Orders containing both", str(p["pair_orders"])),
                                     (f"Share of {p['a_name']} orders", f"{p['pct_of_a']:.0f}%"),
                                     (f"Share of {p['b_name']} orders", f"{p['pct_of_b']:.0f}%"),
                                     ("Lift vs chance", f"{p['lift']:.1f}×"),
                                     ("Combined price / cost", f"{_peso(price)} / {_peso(cost)}"),
                                     ("Estimated weekly profit change", _peso(best["net"]))]},
            "prefill": {"rec_id": rid, "name": f"Bundle: {title}"[:100], "campaign_type": "seasonal", "kind": "percent",
                        "value": d, "buy_quantity": 1, "get_quantity": 1, "scope": "variants", "target_ids": [a, b],
                        "days_of_week": [], "start_time": "", "end_time": "",
                        "start_date": today.isoformat(), "end_date": (today + timedelta(days=27)).isoformat(),
                        "min_order_amount": round(price, 2), "auto_apply": True},
            **_links(rid),
        })
    return recs, skipped


# --------------------------------------------------------------------------- orchestration
def build_recommendations():
    suff = fc.data_sufficiency()
    result = {"has_enough_data": suff["ok"], "sufficiency": suff, "message": "", "insights": fc.basic_insights(),
              "recommendations": [], "top": None, "others_count": 0, "skipped": [], "analysis": {},
              "generated_at": timezone.now()}
    result["analysis"]["trends"] = fc.period_trends()                  # observed history only - safe at any data level
    if not suff["ok"]:
        result["message"] = FALLBACK_MESSAGE                           # no forecasts, no recommendations
        return result

    since, today = fc.window()
    day_hours, day_rev = fc.orders_by_day_hour(since, today)
    profile = fc.hourly_profile(day_hours, day_rev)
    windows = fc.slow_windows(profile)
    perf = fc.product_performance(suff["trading_days"])
    pairs = fc.product_pairs()
    history = fc.promotion_history(day_hours)
    ctx = _build_context(history, perf["all"])

    recs, skipped = [], []
    for part in (_slow_period_recs(ctx, windows, history, perf["all"], suff["trading_days"], suff["total_orders"]),
                 _declining_recs(ctx, perf["declining"], history, suff["trading_days"]),
                 _bundle_recs(ctx, pairs, history, perf["all"], suff["trading_days"], (today - since).days)):
        recs += part[0]
        skipped += part[1]
    recs.sort(key=lambda r: -r["score"])
    recs = recs[:MAX_RECOMMENDATIONS]

    result.update({"recommendations": recs, "top": recs[0] if recs else None, "others_count": max(len(recs) - 1, 0),
                   "skipped": skipped})
    result["analysis"].update({
        "forecast": fc.forecast_next_7_days(), "heatmap": fc.heatmap(profile), "slow_windows": windows,
        "profile": {wd: {"days": v["days"], "hours": {h: x["orders"] for h, x in v["hours"].items()}}
                    for wd, v in profile.items()},
        "products": {k: perf[k] for k in ("best", "slow", "declining")}, "pairs": pairs, "history": history,
        "assumptions": {"elasticity": ctx["elasticity"], "elasticity_source": ctx["elasticity_source"],
                        "cost_coverage_pct": ctx["cost_coverage"] * 100,
                        "min_margin_pct": cfg("PROMO_REC_MIN_MARGIN_PCT", 20)},
    })
    return result


def get_cached_recommendations(force=False):
    if not force:
        hit = cache.get(CACHE_KEY)
        if hit is not None:
            return hit
    data = build_recommendations()
    cache.set(CACHE_KEY, data, cfg("PROMO_REC_CACHE_SECONDS", 300))
    return data


def invalidate_cache():
    cache.delete(CACHE_KEY)


def get_recommendation_by_id(rec_id):
    for r in get_cached_recommendations()["recommendations"]:
        if r["id"] == rec_id:
            return r
    return None
