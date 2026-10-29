"""DEV ONLY - generate a demo menu, recipes (with costs) and N days of realistic sales.

    python manage.py seed_demo_sales              # 60 days ending yesterday
    python manage.py seed_demo_sales --clear      # remove previously generated demo orders first
    python manage.py seed_demo_sales --days 10    # too little data -> the banner shows the fallback message

The demo history contains: quiet afternoons (slow periods), a product whose sales are falling (Chicken Wrap),
and fries that are often ordered with beef wraps (bundle candidate). Demo orders are tagged customer_name="__demo__".
"""
import random
from datetime import datetime, time, timedelta
from decimal import Decimal as D

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.models import Ingredient, RecipeItem
from point_of_sale.models import Category, MenuItem, MenuVariant, Order, OrderItem

DEMO_TAG = "__demo__"
# (name, unit, stock, reorder, cost per unit)
INGREDIENTS = [("Beef Slices", "kg", 60, 5, 520), ("Chicken Slices", "kg", 60, 5, 260), ("Pita Bread", "pcs", 900, 40, 8),
               ("Potatoes (fries)", "kg", 80, 4, 150), ("Garlic Sauce", "L", 25, 2, 320), ("Soda (can)", "pcs", 500, 40, 15)]
# (category, item, variant, price, recipe {ingredient: qty})
MENU = [("Wraps", "Beef Shawarma Wrap", "Standard", 150, {"Beef Slices": .1, "Pita Bread": 1, "Garlic Sauce": .02}),
        ("Wraps", "Beef Shawarma Wrap", "Large", 210, {"Beef Slices": .16, "Pita Bread": 1, "Garlic Sauce": .03}),
        ("Wraps", "Chicken Shawarma Wrap", "Standard", 130, {"Chicken Slices": .1, "Pita Bread": 1, "Garlic Sauce": .02}),
        ("Sides", "French Fries", "Regular", 80, {"Potatoes (fries)": .2}),
        ("Drinks", "Soda", "Can", 40, {"Soda (can)": 1})]
HOUR_WEIGHT = {10: 3, 11: 6, 12: 12, 13: 11, 14: 3, 15: 2, 16: 2, 17: 9, 18: 10, 19: 12, 20: 9, 21: 4}


class Command(BaseCommand):
    help = "DEV ONLY: demo menu + recipes + generated sales history for the Smart Promotion banner."

    def add_arguments(self, p):
        p.add_argument("--days", type=int, default=60)
        p.add_argument("--orders-per-day", type=int, default=45)
        p.add_argument("--clear", action="store_true", help="delete previously generated demo orders first")
        p.add_argument("--seed", type=int, default=7)

    @transaction.atomic
    def handle(self, *a, **o):
        if not settings.DEBUG:
            raise CommandError("seed_demo_sales is for development only (DEBUG is False).")
        if o["clear"]:
            n = Order.objects.filter(customer_name=DEMO_TAG).delete()[0]
            self.stdout.write(f"Removed {n} demo rows.")

        ing = {}
        for name, unit, stock, reorder, cost in INGREDIENTS:
            ing[name], _ = Ingredient.objects.get_or_create(name=name, defaults=dict(
                unit=unit, current_stock=stock, reorder_level=reorder, cost_per_unit=cost))
        variants = {}
        for cat, item, vname, price, recipe in MENU:
            c, _ = Category.objects.get_or_create(name=cat, defaults={"slug": cat.lower()})
            mi, _ = MenuItem.objects.get_or_create(name=item, defaults={"category": c})
            v, _ = MenuVariant.objects.get_or_create(item=mi, name=vname, defaults={"price": D(price)})
            variants[(item, vname)] = v
            for iname, qty in recipe.items():
                RecipeItem.objects.get_or_create(variant=v, ingredient=ing[iname], defaults={"quantity_required": D(str(qty))})

        beef_s, beef_l = variants[("Beef Shawarma Wrap", "Standard")], variants[("Beef Shawarma Wrap", "Large")]
        chk, fries, soda = variants[("Chicken Shawarma Wrap", "Standard")], variants[("French Fries", "Regular")], variants[("Soda", "Can")]
        rng, today, total = random.Random(o["seed"]), timezone.localdate(), 0
        wsum = sum(HOUR_WEIGHT.values())

        for back in range(o["days"], 0, -1):
            day = today - timedelta(days=back)
            recent = back <= 14
            weekend = 1.25 if day.weekday() >= 5 else 1.0
            for hour, w in HOUR_WEIGHT.items():
                mu = o["orders_per_day"] * weekend * w / wsum
                for _ in range(max(0, round(rng.gauss(mu, mu ** 0.5)))):
                    when = timezone.make_aware(datetime.combine(day, time(hour, rng.randint(0, 59))))
                    lines = []
                    r = rng.random()
                    wrap = beef_s if r < .40 else beef_l if r < .55 else chk if r < (.62 if recent else .88) else None
                    if wrap is None:
                        wrap = beef_s
                    lines.append(wrap)
                    if wrap in (beef_s, beef_l) and rng.random() < .5:
                        lines.append(fries)
                    elif rng.random() < .15:
                        lines.append(fries)
                    if rng.random() < .25:
                        lines.append(soda)
                    order = Order.objects.create(created_at=when, status=Order.Status.COMPLETED, completed_at=when,
                                                 last_status_at=when, customer_name=DEMO_TAG)
                    for v in lines:
                        OrderItem.objects.create(order=order, variant=v, item_name=str(v), quantity=1, unit_price=v.price)
                    order.recalculate_totals()
                    total += 1
        self.stdout.write(self.style.SUCCESS(f"Created {total} demo orders over {o['days']} days. Open the dashboard."))
