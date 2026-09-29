from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# codename lists per role. "app.codename" => resolved against Permission table.
CASHIER = [
    "point_of_sale.add_order", "point_of_sale.view_order", "point_of_sale.change_order",
    "point_of_sale.add_orderitem", "point_of_sale.view_menuitem", "point_of_sale.view_menuvariant",
    "point_of_sale.view_ready_alerts", "point_of_sale.complete_order", "point_of_sale.apply_manual_discount",
    "billing_payment.add_payment", "billing_payment.view_payment", "billing_payment.view_receipt",
    "billing_payment.add_receipt",
]
KITCHEN = [
    "point_of_sale.view_kds", "point_of_sale.bump_order", "point_of_sale.view_order",
    "inventory.view_ingredient",
]
MANAGER_EXTRA = [
    "point_of_sale.void_order", "point_of_sale.add_menuitem", "point_of_sale.change_menuitem",
    "point_of_sale.add_menuvariant", "point_of_sale.change_menuvariant", "point_of_sale.view_kds",
    "point_of_sale.bump_order",
    "inventory.add_ingredient", "inventory.change_ingredient", "inventory.view_ingredient",
    "inventory.adjust_stock", "inventory.add_recipe", "inventory.change_recipe", "inventory.view_recipe",
    "inventory.view_stockmovement", "inventory.view_lowstockalert", "inventory.change_lowstockalert",
    "promotions.add_promotion", "promotions.change_promotion", "promotions.view_promotion",
    "reports_analytics.view_dailysalessummary", "reports_analytics.view_demandforecast",
    "reports_analytics.view_promotionsuggestion", "reports_analytics.change_promotionsuggestion",
    "security.manage_employees", "security.view_all_attendance", "security.view_employeeprofile",
    "security.add_shift", "security.change_shift", "activity_log.view_activitylog",
    "billing_payment.refund_payment",
]


class Command(BaseCommand):
    help = "Create Cashier / Kitchen / Manager groups and attach permissions."

    def handle(self, *args, **opts):
        plan = {"Cashier": CASHIER, "Kitchen": KITCHEN, "Manager": CASHIER + KITCHEN + MANAGER_EXTRA}
        for group_name, perms in plan.items():
            group, _ = Group.objects.get_or_create(name=group_name)
            objs = []
            for full in sorted(set(perms)):
                app, codename = full.split(".")
                try:
                    objs.append(Permission.objects.get(content_type__app_label=app, codename=codename))
                except Permission.DoesNotExist:
                    self.stderr.write(f"  ! missing permission {full} (run migrate first)")
            group.permissions.set(objs)
            self.stdout.write(self.style.SUCCESS(f"{group_name}: {len(objs)} permissions"))
