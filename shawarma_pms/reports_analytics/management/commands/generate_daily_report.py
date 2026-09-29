from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reports_analytics import services


class Command(BaseCommand):
    help = "Roll up sales for a day (default: today), then refresh slow-period suggestions and forecasts."

    def add_arguments(self, p):
        p.add_argument("--date", help="YYYY-MM-DD (default today)")
        p.add_argument("--backfill", type=int, default=0, help="Also rebuild the previous N days")

    def handle(self, *a, **o):
        end = date.fromisoformat(o["date"]) if o["date"] else timezone.localdate()
        for i in range(o["backfill"], -1, -1):
            s = services.build_daily_summary(end - timedelta(days=i))
            self.stdout.write(f"{s.date}: {s.order_count} orders, net {s.net_sales}, avg ticket {s.avg_ticket_seconds}s")
        new = services.detect_slow_periods()
        fc = services.forecast_ingredient_demand()
        self.stdout.write(self.style.SUCCESS(f"{len(new)} new promo suggestions, {len(fc)} forecasts updated."))
