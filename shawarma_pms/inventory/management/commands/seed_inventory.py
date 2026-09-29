from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import Ingredient, Recipe, RecipeLine
from point_of_sale.models import Category, MenuItem, MenuVariant

# (name, sku, category, unit, opening stock, reorder level, par, cost, perishable, shelf life days)
INGREDIENTS = [
    ("Marinated Beef Slices",   "PRO-BEEF",  "protein",   "kg",  "20",  "5",  "25",  "520", True,  3),
    ("Marinated Chicken Slices","PRO-CHKN",  "protein",   "kg",  "20",  "5",  "25",  "260", True,  3),
    ("Pita Bread (Standard)",   "CAR-PITA-S","carb",      "pcs", "200", "40", "250", "8",   True,  3),
    ("Pita Bread (Large)",      "CAR-PITA-L","carb",      "pcs", "120", "25", "150", "12",  True,  3),
    ("French Fries",            "CAR-FRY",   "carb",      "kg",  "15",  "4",  "20",  "150", False, None),
    ("Cucumbers",               "PRD-CUC",   "produce",   "kg",  "6",   "2",  "8",   "60",  True,  5),
    ("Tomatoes",                "PRD-TOM",   "produce",   "kg",  "6",   "2",  "8",   "90",  True,  5),
    ("White Onions",            "PRD-ONI",   "produce",   "kg",  "6",   "2",  "8",   "80",  True,  14),
    ("Shredded Cabbage",        "PRD-CAB",   "produce",   "kg",  "5",   "2",  "8",   "70",  True,  5),
    ("Garlic Sauce (Toum)",     "SAU-TOUM",  "sauce",     "L",   "8",   "2",  "10",  "320", True,  7),
    ("Cheese Sauce",            "SAU-CHS",   "sauce",     "L",   "5",   "1.5","8",   "280", True,  7),
    ("Chili/Hot Sauce",         "SAU-HOT",   "sauce",     "L",   "5",   "1.5","8",   "200", False, None),
    ("Shawarma Foil Wrappers",  "PKG-FOIL",  "packaging", "pcs", "500", "100","600","1.5", False, None),
    ("Paper Takeout Bags",      "PKG-BAG",   "packaging", "pcs", "300", "60", "400","2",   False, None),
]

# Beef Shawarma Wrap (Standard): 1 pita, 0.1 kg beef, 0.05 kg mixed veg, 0.02 L toum, 1 foil.
# "Mixed vegetables" (0.05 kg) is split across the four produce SKUs so each is tracked individually.
BEEF_WRAP = [("Pita Bread (Standard)", "1"), ("Marinated Beef Slices", "0.100"),
             ("Cucumbers", "0.015"), ("Tomatoes", "0.015"), ("White Onions", "0.010"),
             ("Shredded Cabbage", "0.010"),          # 0.015+0.015+0.010+0.010 = 0.050 kg
             ("Garlic Sauce (Toum)", "0.020"), ("Shawarma Foil Wrappers", "1")]
CHICKEN_WRAP = [(n if n != "Marinated Beef Slices" else "Marinated Chicken Slices", q) for n, q in BEEF_WRAP]


class Command(BaseCommand):
    help = "Seed raw ingredients, a sample menu and the Beef/Chicken Shawarma recipes."

    @transaction.atomic
    def handle(self, *args, **opts):
        for n, sku, cat, unit, stock, low, par, cost, per, life in INGREDIENTS:
            Ingredient.objects.update_or_create(sku=sku, defaults=dict(
                name=n, category=cat, unit=unit, current_stock=D(stock), reorder_level=D(low),
                par_level=D(par), cost_per_unit=D(cost), is_perishable=per, shelf_life_days=life))

        wraps = Category.objects.get_or_create(name="Shawarma Wraps", defaults={"slug": "wraps"})[0]
        sides = Category.objects.get_or_create(name="Sides", defaults={"slug": "sides"})[0]
        for cat, name, variants in [
            (wraps, "Beef Shawarma Wrap", [("Standard", "BSW-S", "150"), ("Large", "BSW-L", "210")]),
            (wraps, "Chicken Shawarma Wrap", [("Standard", "CSW-S", "130"), ("Large", "CSW-L", "185")]),
            (sides, "French Fries", [("Regular", "FRY-R", "80")]),
        ]:
            item = MenuItem.objects.get_or_create(name=name, defaults={"category": cat})[0]
            for vname, sku, price in variants:
                MenuVariant.objects.get_or_create(sku=sku, defaults={"item": item, "name": vname, "price": D(price)})

        for sku, lines in [("BSW-S", BEEF_WRAP), ("CSW-S", CHICKEN_WRAP)]:
            recipe, _ = Recipe.objects.get_or_create(variant=MenuVariant.objects.get(sku=sku))
            recipe.lines.all().delete()
            for ing_name, qty in lines:
                RecipeLine.objects.create(recipe=recipe, ingredient=Ingredient.objects.get(name=ing_name),
                                          quantity=D(qty))
        self.stdout.write(self.style.SUCCESS("Inventory, menu and recipes seeded."))
