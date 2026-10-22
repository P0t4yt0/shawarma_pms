"""Tests for Smart Promotion Recommendations, the promotions engine wiring and the POS checkout.

Run:  python manage.py test reports_analytics
"""
import json
from datetime import datetime
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from inventory.models import Ingredient, RecipeItem
from point_of_sale.models import MenuVariant, Order
from promotions.engine import apply_promotions, preview_discounts
from promotions.models import AppliedDiscount, Promotion
from reports_analytics import forecasting as fc
from reports_analytics.recommendations import FALLBACK_MESSAGE, build_recommendations, get_cached_recommendations


def make_user(name, perms=()):
    u = User.objects.create_user(name, password="pw")
    for full in perms:
        app, code = full.split(".")
        u.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
    return u


def seed(days):
    with override_settings(DEBUG=True):
        call_command("seed_demo_sales", days=days, stdout=StringIO())


MANAGER = ["promotions.view_promotion", "promotions.add_promotion", "promotions.change_promotion",
           "reports_analytics.view_demandforecast"]


class FallbackTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_no_data_shows_only_the_fallback(self):
        d = build_recommendations()
        self.assertFalse(d["has_enough_data"])
        self.assertEqual(d["message"], FALLBACK_MESSAGE)
        self.assertEqual(d["recommendations"], [])
        self.assertNotIn("forecast", d["analysis"])          # no unsupported forecast is produced

    def test_too_few_days_still_falls_back(self):
        seed(10)
        d = build_recommendations()
        self.assertFalse(d["has_enough_data"])
        self.assertEqual(d["recommendations"], [])
        self.assertTrue(d["insights"]["orders_7d"] > 0)       # basic facts are still available
        self.assertNotIn("forecast", d["analysis"])

    def test_today_is_excluded(self):
        seed(60)
        self.assertTrue(all(r["date"] < timezone.localdate() for r in fc.daily_series()))


class RecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed(60)

    def setUp(self):
        cache.clear()

    def test_recommendations_are_data_driven_and_complete(self):
        d = build_recommendations()
        self.assertTrue(d["has_enough_data"])
        types = {r["type"] for r in d["recommendations"]}
        self.assertIn("slow_period", types)
        self.assertIn("declining_product", types)
        self.assertIn("bundle", types)
        scores = [r["score"] for r in d["recommendations"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for r in d["recommendations"]:
            for key in ("explanation", "estimate", "prefill", "report_url", "create_url", "guardrails", "evidence"):
                self.assertTrue(r[key], key)
            self.assertIn("?rec=" + r["id"], r["create_url"])
            self.assertGreater(r["prefill"]["value"], 0)

    def test_slow_period_prefill_matches_the_data(self):
        d = build_recommendations()
        r = next(x for x in d["recommendations"] if x["type"] == "slow_period")
        p = r["prefill"]
        self.assertEqual(p["campaign_type"], "happy_hour")
        self.assertEqual(p["scope"], "all")
        self.assertEqual(len(p["days_of_week"]), 1)
        self.assertEqual(p["start_time"], "14:00")            # the seeded quiet window is 14:00-17:00
        self.assertEqual(p["end_time"], "16:59")

    def test_declining_product_is_the_chicken_wrap(self):
        d = build_recommendations()
        r = next(x for x in d["recommendations"] if x["type"] == "declining_product")
        self.assertIn("Chicken", r["title"])
        self.assertEqual(r["prefill"]["scope"], "variants")
        self.assertEqual(r["prefill"]["target_ids"], [MenuVariant.objects.get(item__name__startswith="Chicken").id])

    def test_profit_guardrail_discount_never_breaks_margin_floor(self):
        d = build_recommendations()
        for r in d["recommendations"]:
            margin = float(r["guardrails"][0].split("≈ ")[1].split("%")[0])
            self.assertGreaterEqual(margin, 20)

    def test_unknown_margin_blocks_every_recommendation(self):
        RecipeItem.objects.all().delete()
        d = build_recommendations()
        self.assertEqual(d["recommendations"], [])
        self.assertTrue(any("unknown" in s.lower() for s in d["skipped"]))

    def test_stock_guardrail_blocks_when_ingredient_is_short(self):
        Ingredient.objects.filter(name="Pita Bread").update(current_stock=D("2"))
        d = build_recommendations()
        self.assertEqual([r for r in d["recommendations"] if r["type"] in ("slow_period", "declining_product")], [])
        self.assertTrue(any("Pita Bread" in s for s in d["skipped"]))

    def test_existing_promotion_suppresses_duplicate_recommendation(self):
        before = build_recommendations()
        slow = next(r for r in before["recommendations"] if r["type"] == "slow_period")
        wd = slow["prefill"]["days_of_week"][0]
        Promotion.objects.create(name="Already running", campaign_type="happy_hour", kind="percent", value=10,
                                 days_of_week=[wd], start_time=datetime(2000, 1, 1, 14).time(),
                                 end_time=datetime(2000, 1, 1, 16, 59).time())
        after = build_recommendations()
        self.assertNotIn(slow["id"], [r["id"] for r in after["recommendations"]])

    def test_cache_and_lookup_by_id(self):
        first = get_cached_recommendations()
        self.assertEqual(get_cached_recommendations()["generated_at"], first["generated_at"])   # served from cache
        top = first["top"]
        from reports_analytics.recommendations import get_recommendation_by_id
        self.assertEqual(get_recommendation_by_id(top["id"])["id"], top["id"])
        self.assertIsNone(get_recommendation_by_id("does-not-exist"))


class EngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed(1)
        cls.beef = MenuVariant.objects.get(item__name="Beef Shawarma Wrap", name="Standard")
        cls.fries = MenuVariant.objects.get(item__name="French Fries")
        tz = timezone.get_current_timezone()
        cls.tue_3pm = timezone.make_aware(datetime(2026, 10, 20, 15, 0), tz)     # a Tuesday
        cls.tue_6pm = timezone.make_aware(datetime(2026, 10, 20, 18, 0), tz)

    def promo(self, **kw):
        base = dict(name="HH", campaign_type="happy_hour", kind="percent", value=20, scope="variants",
                    days_of_week=[1], start_time=datetime(2000, 1, 1, 14).time(), end_time=datetime(2000, 1, 1, 16, 59).time())
        base.update(kw)
        p = Promotion.objects.create(**base)
        if base["scope"] == "variants":
            p.variants.add(self.beef)
        return p

    def lines(self):
        return [(self.beef, 1, self.beef.price), (self.fries, 1, self.fries.price)]

    def test_scoped_happy_hour_applies_only_inside_window_and_to_its_items(self):
        self.promo()
        inside = preview_discounts(self.lines(), at=self.tue_3pm)
        self.assertEqual(inside[0][0][1], D("30.00"))        # 20% of 150
        self.assertEqual(inside[1], [])                      # fries are not in scope
        self.assertEqual(preview_discounts(self.lines(), at=self.tue_6pm), [[], []])

    def test_manual_campaign_never_auto_applies(self):
        m = self.promo(name="Senior", campaign_type="manual", scope="all", days_of_week=[], start_time=None,
                       end_time=None, value=10, auto_apply=True)          # even if auto_apply was left True
        self.assertEqual(preview_discounts(self.lines(), at=self.tue_3pm), [[], []])
        picked = preview_discounts(self.lines(), at=self.tue_3pm, manual_promo_ids=(m.pk,))
        self.assertEqual(picked[0][0][1], D("15.00"))

    def test_min_order_amount_gates_bundle_style_promo(self):
        self.promo(name="Bundle", campaign_type="seasonal", scope="variants", days_of_week=[], start_time=None,
                   end_time=None, value=10, min_order_amount=D("230"))
        self.assertEqual(preview_discounts([(self.beef, 1, self.beef.price)], at=self.tue_3pm), [[]])   # 150 < 230
        both = preview_discounts(self.lines(), at=self.tue_3pm)                                           # 230 >= 230
        self.assertEqual(both[0][0][1], D("15.00"))

    def test_preview_equals_what_apply_saves(self):
        self.promo(scope="all", days_of_week=[], start_time=None, end_time=None, value=10)
        order = Order.objects.create()
        from point_of_sale.models import OrderItem
        for v in (self.beef, self.fries):
            OrderItem.objects.create(order=order, variant=v, item_name=str(v), quantity=2, unit_price=v.price)
        plan = preview_discounts([(self.beef, 2, self.beef.price), (self.fries, 2, self.fries.price)])
        apply_promotions(order)
        order.recalculate_totals()
        self.assertEqual(order.discount_total, sum(a for p in plan for _, a in p))
        self.assertEqual(AppliedDiscount.objects.filter(order=order).count(), 2)


class PosCheckoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed(1)
        cls.cashier = make_user("cash", ["point_of_sale.add_order"])
        cls.beef = MenuVariant.objects.get(item__name="Beef Shawarma Wrap", name="Standard")

    def setUp(self):
        self.client.force_login(self.cashier)
        Order.objects.filter(customer_name="").delete()

    def post(self, **extra):
        body = {"items": [{"id": self.beef.id, "qty": 2, "discount": 9999}], "payment_method": "cash", "tendered": 1000}
        body.update(extra)
        return self.client.post(reverse("pos:pos_screen"), json.dumps(body), content_type="application/json").json()

    def test_auto_promo_is_applied_on_the_server_and_client_discount_is_ignored(self):
        Promotion.objects.create(name="Always 10", campaign_type="seasonal", kind="percent", value=10, scope="all")
        r = self.post()
        self.assertTrue(r["success"], r)
        self.assertEqual(D(r["discount_total"]), D("30.00"))      # 10% of 300, NOT the 9999 the client sent
        self.assertEqual(D(r["total"]), D("270.00"))
        self.assertEqual(AppliedDiscount.objects.filter(order__order_number=r["order_number"]).count(), 1)

    def test_manual_promo_needs_selection(self):
        m = Promotion.objects.create(name="Senior", campaign_type="manual", kind="percent", value=20, scope="all")
        self.assertEqual(D(self.post()["discount_total"]), D("0"))
        self.assertEqual(D(self.post(applied_promo_id=m.id)["discount_total"]), D("60.00"))

    def test_cash_tendered_below_total_is_rejected_and_rolled_back(self):
        n = Order.objects.count()
        r = self.post(tendered=10)
        self.assertFalse(r["success"])
        self.assertEqual(Order.objects.count(), n)

    def test_preview_endpoint_matches_checkout(self):
        Promotion.objects.create(name="Always 10", campaign_type="seasonal", kind="percent", value=10, scope="all")
        pv = self.client.post(reverse("pos:promo_preview"), json.dumps({"items": [{"id": self.beef.id, "qty": 2}]}),
                              content_type="application/json").json()
        self.assertTrue(pv["success"])
        self.assertEqual(pv["lines"][0]["discount"], 30.0)
        self.assertEqual(pv["promo_names"], ["Always 10"])


class DashboardAndReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed(60)
        cls.manager = make_user("mgr", MANAGER)
        cls.cashier = make_user("cash2", ["point_of_sale.add_order"])

    def setUp(self):
        cache.clear()

    def test_manager_sees_banner_with_both_buttons(self):
        self.client.force_login(self.manager)
        html = self.client.get(reverse("security:dashboard")).content.decode()
        self.assertIn("Smart Promotion", html)
        self.assertIn("View Sales Report", html)
        self.assertIn("Create Promotion", html)
        self.assertLess(html.index("Smart Promotion"), html.index('class="kpi-grid"'))     # banner is above the KPIs
        top = get_cached_recommendations()["top"]
        self.assertIn(top["create_url"], html)
        self.assertIn(top["report_url"].replace("&", "&amp;"), html)

    def test_cashier_does_not_see_banner(self):
        self.client.force_login(self.cashier)
        self.assertNotIn("Smart Promotion", self.client.get(reverse("security:dashboard")).content.decode())

    def test_buttons_follow_permissions(self):
        only_report = make_user("rep", ["reports_analytics.view_demandforecast"])
        self.client.force_login(only_report)
        html = self.client.get(reverse("security:dashboard")).content.decode()
        self.assertIn("View Sales Report", html)
        self.assertNotIn("Create Promotion", html)

    def test_report_shows_evidence_and_blocks_cashiers(self):
        top = get_cached_recommendations()["top"]
        self.client.force_login(self.manager)
        r = self.client.get(top["report_url"])
        self.assertContains(r, "Safety checks passed")
        self.assertContains(r, "Weekly sales")
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(top["report_url"]).status_code, 403)

    def test_report_with_unknown_rec_is_graceful(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("reports_analytics:sales_forecast_report") + "?rec=nope"),
                            "no longer available")


class FallbackBannerTests(TestCase):
    def test_fallback_message_is_rendered_exactly(self):
        cache.clear()
        self.client.force_login(make_user("mgr3", MANAGER))
        html = self.client.get(reverse("security:dashboard")).content.decode()
        self.assertIn(FALLBACK_MESSAGE, html)
        self.assertNotIn("Create Promotion", html)            # nothing to create from
        r = self.client.get(reverse("reports_analytics:sales_forecast_report"))
        self.assertContains(r, "Not enough sales data yet")
        self.assertNotContains(r, "Next 7 days forecast")


class PromotionFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed(60)
        cls.manager = make_user("mgr4", MANAGER)
        cls.cashier = make_user("cash4", ["point_of_sale.add_order"])

    def setUp(self):
        cache.clear()
        self.client.force_login(self.manager)

    def add(self, **kw):
        body = {"action": "add", "name": "Test", "campaign_type": "happy_hour", "kind": "percent", "value": 15,
                "scope": "all", "target_ids": []}
        body.update(kw)
        return self.client.post(reverse("promotions:list"), json.dumps(body), content_type="application/json").json()

    def test_form_opens_prefilled_from_recommendation(self):
        top = get_cached_recommendations()["top"]
        r = self.client.get(top["create_url"])
        prefill = json.loads(r.context["prefill_json"])
        self.assertEqual(prefill["rec_id"], top["id"])
        self.assertEqual(prefill["name"], top["prefill"]["name"])
        self.assertContains(r, "const prefillData = JSON.parse(")

    def test_saving_a_prefilled_promotion_persists_all_fields(self):
        top = get_cached_recommendations()["top"]
        res = self.add(**{k: v for k, v in top["prefill"].items() if k != "rec_id"})
        self.assertTrue(res["success"], res)
        p = Promotion.objects.get(name=top["prefill"]["name"])
        self.assertEqual(p.days_of_week, top["prefill"]["days_of_week"])
        self.assertEqual(p.start_time.hour, 14)
        self.assertTrue(p.auto_apply)
        self.assertEqual(p.created_by, self.manager)
        self.assertNotIn(top["id"], [r["id"] for r in get_cached_recommendations()["recommendations"]])  # no duplicate

    def test_multi_variant_scope_and_validation(self):
        ids = list(MenuVariant.objects.values_list("id", flat=True)[:2])
        self.assertTrue(self.add(scope="variants", target_ids=ids, name="Two")["success"])
        self.assertEqual(Promotion.objects.get(name="Two").variants.count(), 2)
        self.assertFalse(self.add(start_time="14:00")["success"])             # start without end
        self.assertFalse(self.add(scope="variants", target_ids=[])["success"])
        self.assertFalse(self.add(value=0)["success"])

    def test_manual_campaign_is_never_auto(self):
        self.add(campaign_type="manual", name="Manual one", auto_apply=True)
        self.assertFalse(Promotion.objects.get(name="Manual one").auto_apply)

    def test_permissions(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("promotions:list")).status_code, 403)
