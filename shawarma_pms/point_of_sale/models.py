"""point_of_sale app: menu, orders, the KDS state machine and its audit/event log.

Order lifecycle (state machine)
    pending -> preparing -> ready_to_serve -> completed        (+ cancelled from pending/preparing)
      ^ ticket captured     ^ kitchen        ^ kitchen bumps      ^ cashier hands over = final bump

Every transition writes an OrderStatusLog row. That table is used three ways:
  1. kitchen-efficiency analytics (durations between states, ticket time),
  2. the cursor-based event feed for Phase 1 JS polling,
  3. the exact same event payload that Phase 2 pushes over WebSockets.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from .signals import order_status_changed


TWO = Decimal("0.01")


# --------------------------------------------------------------------------- catalog
class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category__name", "name"]

    def __str__(self):
        return self.name


class MenuVariant(models.Model):
    # Ibinalik natin ang mga original na fields na kailangan ng system mo
    item = models.ForeignKey('MenuItem', on_delete=models.CASCADE, related_name='variants')
    name = models.CharField(max_length=100)
    
    # Ang ating auto-generating SKU field
    sku = models.CharField(max_length=50, unique=True, blank=True)
    
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.item.name} - {self.name}"

    # Ang ating Automatic Product ID / SKU Generator
    def save(self, *args, **kwargs):
        if not self.sku:
            is_unique = False
            while not is_unique:
                # Gagamit tayo ng PRD- prefix para sa mga Products
                new_sku = 'PRD-' + get_random_string(8).upper()
                if not MenuVariant.objects.filter(sku=new_sku).exists():
                    self.sku = new_sku
                    is_unique = True
                    
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------- orders
class OrderCounter(models.Model):
    """Per-day sequence so order numbers restart every day (row-locked, no duplicates)."""
    date = models.DateField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)

    @classmethod
    def next_number(cls, date):
        counter, _ = cls.objects.select_for_update().get_or_create(date=date)
        counter.last_number += 1
        counter.save(update_fields=["last_number"])
        return f"{date:%y%m%d}-{counter.last_number:04d}"


class InvalidTransition(Exception):
    pass


class Order(models.Model):
    class Type(models.TextChoices):
        WALK_IN = "walk_in", "Walk-in (dine-in)"
        TAKEOUT = "takeout", "Takeout"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PREPARING = "preparing", "Preparing"
        READY = "ready_to_serve", "Ready to serve"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"      # void path; not part of the 4-step happy path

    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.PREPARING, Status.CANCELLED},
        Status.PREPARING: {Status.READY, Status.CANCELLED},
        Status.READY: {Status.COMPLETED},
        Status.COMPLETED: set(),
        Status.CANCELLED: set(),
    }
    # Django permission needed to move INTO each state (checked by the API view)
    REQUIRED_PERM = {
        Status.PREPARING: "point_of_sale.bump_order",
        Status.READY: "point_of_sale.bump_order",
        Status.COMPLETED: "point_of_sale.complete_order",
        Status.CANCELLED: "point_of_sale.void_order",
    }
    ACTIVE_KDS_STATUSES = (Status.PENDING, Status.PREPARING)

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    order_type = models.CharField(max_length=10, choices=Type.choices, default=Type.WALK_IN)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                related_name="orders_taken")
    customer_name = models.CharField(max_length=80, blank=True)     # shown on the KDS / call-out
    notes = models.CharField(max_length=250, blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)      # before discounts
    discount_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)         # amount due
    tax_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)     # VAT included in total

    # Timestamps for every state (ticket time = completed_at - created_at)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)          # ticket captured
    preparing_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)                      # final bump
    cancelled_at = models.DateTimeField(null=True, blank=True)
    last_status_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"]),
                   models.Index(fields=["created_at", "status"])]
        permissions = [
            ("view_kds", "Can open the Kitchen Display System"),
            ("bump_order", "Can advance kitchen states (pending/preparing/ready)"),
            ("complete_order", "Can complete (final bump) a ready order"),
            ("view_ready_alerts", "Can receive ready-to-serve alerts"),
            ("apply_manual_discount", "Can apply manual/percentage discounts"),
            ("void_order", "Can cancel/void orders"),
        ]

    def __str__(self):
        return f"#{self.order_number} [{self.status}]"

    # ---- persistence
    def save(self, *args, **kwargs):
        if not self.order_number:
            with transaction.atomic():
                self.order_number = OrderCounter.next_number(timezone.localdate(self.created_at))
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    # ---- totals
    def recalculate_totals(self, save=True):
        items = list(self.items.all())
        self.subtotal = sum((i.gross for i in items), Decimal("0"))
        self.discount_total = sum((i.discount_amount for i in items), Decimal("0"))
        self.total = max(self.subtotal - self.discount_total, Decimal("0")).quantize(TWO, ROUND_HALF_UP)
        rate = Decimal(str(getattr(settings, "POS_VAT_RATE", 0)))
        self.tax_total = (self.total * rate / (1 + rate)).quantize(TWO, ROUND_HALF_UP) if rate else Decimal("0")
        if save:
            self.save(update_fields=["subtotal", "discount_total", "total", "tax_total"])

    @property
    def amount_paid(self):
        # Payment lives in billing_payment (related_name="payments"); refunded/voided rows excluded.
        return sum((p.amount for p in self.payments.filter(status="completed")), Decimal("0"))

    @property
    def balance_due(self):
        return max(self.total - self.amount_paid, Decimal("0"))

    @property
    def is_paid(self):
        return self.balance_due == 0 and self.total > 0

    # ---- kitchen KPIs (seconds); None until the milestone is reached
    @staticmethod
    def _secs(a, b):
        return int((b - a).total_seconds()) if a and b else None

    @property
    def wait_seconds(self):          # captured -> kitchen starts
        return self._secs(self.created_at, self.preparing_at)

    @property
    def prep_seconds(self):          # kitchen starts -> ready
        return self._secs(self.preparing_at, self.ready_at)

    @property
    def pickup_seconds(self):        # ready -> handed over
        return self._secs(self.ready_at, self.completed_at)

    @property
    def ticket_seconds(self):        # capture -> final bump
        return self._secs(self.created_at, self.completed_at)

    @property
    def elapsed_seconds(self):       # live age of an open ticket (KDS timer)
        return int((timezone.now() - self.created_at).total_seconds())

    # ---- state machine
    @transaction.atomic
    def transition_to(self, new_status, user=None, note=""):
        """
        Validate + apply a status change under a row lock, stamp the timestamp column,
        write an OrderStatusLog and (after commit) fire `order_status_changed`.
        """
        locked = Order.objects.select_for_update().get(pk=self.pk)
        new_status = self.Status(new_status)
        if new_status not in self.ALLOWED_TRANSITIONS[self.Status(locked.status)]:
            raise InvalidTransition(f"Cannot move order {locked.order_number} from {locked.status} to {new_status}.")

        now = timezone.now()
        log = OrderStatusLog.objects.create(
            order=locked, from_status=locked.status, to_status=new_status, changed_by=user, changed_at=now,
            seconds_in_previous=int((now - locked.last_status_at).total_seconds()), note=note)

        stamp = {self.Status.PREPARING: "preparing_at", self.Status.READY: "ready_at",
                 self.Status.COMPLETED: "completed_at", self.Status.CANCELLED: "cancelled_at"}[new_status]
        setattr(locked, stamp, now)
        locked.status, locked.last_status_at = new_status, now
        locked.save(update_fields=[stamp, "status", "last_status_at"])

        if new_status == self.Status.CANCELLED:      # give the ingredients back
            from .services import restore_order_stock
            restore_order_stock(locked, user)

        transaction.on_commit(lambda: order_status_changed.send(sender=Order, order=locked, log=log))
        for f in ("status", "last_status_at", stamp):
            setattr(self, f, getattr(locked, f))
        return log


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(MenuVariant, on_delete=models.PROTECT, related_name="order_items")
    item_name = models.CharField(max_length=160)                   # snapshot: survives menu edits
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2)   # snapshot
    discount_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True)        # "no onions", "extra garlic"

    class Meta:
        indexes = [models.Index(fields=["variant", "order"])]

    def __str__(self):
        return f"{self.quantity} x {self.item_name}"

    @property
    def gross(self):
        return self.unit_price * self.quantity

    @property
    def net(self):
        return self.gross - self.discount_amount


class OrderStatusLog(models.Model):
    """One row per transition. `id` is a monotonically increasing cursor for polling AND websockets."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_logs")
    from_status = models.CharField(max_length=20, choices=Order.Status.choices)
    to_status = models.CharField(max_length=20, choices=Order.Status.choices, db_index=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)
    seconds_in_previous = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.order_number}: {self.from_status} -> {self.to_status}"
