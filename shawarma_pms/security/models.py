"""security app: employees, roles, shift schedules and attendance.

Roles are implemented with Django's built-in Groups + Permissions (no custom auth backend):
    Cashier  -> create orders, take payments, see "ready" alerts
    Kitchen  -> KDS view, advance tickets
    Manager  -> everything above + inventory, promotions, reports, employees
Groups are created by `python manage.py setup_roles`.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User, Permission


class Role(models.TextChoices):
    CASHIER = "cashier", "Cashier"
    KITCHEN = "kitchen", "Kitchen"
    MANAGER = "manager", "Manager"


ROLE_GROUP_NAMES = {Role.CASHIER: "Cashier", Role.KITCHEN: "Kitchen", Role.MANAGER: "Manager"}


class EmployeeProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee")
    employee_id = models.CharField(max_length=20, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CASHIER)
    phone = models.CharField(max_length=30, blank=True)
    hire_date = models.DateField(default=timezone.localdate)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    is_active_employee = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_id"]
        permissions = [
            ("manage_employees", "Can create/edit employees and shifts"),
            ("view_all_attendance", "Can view attendance of all employees"),
        ]

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.username}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sync_group()

    def sync_group(self):
        """Keep the user's role Group in sync with `role` (single source of truth for permissions)."""
        groups = Group.objects.filter(name__in=ROLE_GROUP_NAMES.values())
        self.user.groups.remove(*groups)
        group, _ = Group.objects.get_or_create(name=ROLE_GROUP_NAMES[self.role])
        self.user.groups.add(group)


class Shift(models.Model):
    """Scheduled shift (the plan). Attendance records what actually happened."""
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name="shifts")
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    role = models.CharField(max_length=20, choices=Role.choices)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["date", "start_time"]
        indexes = [models.Index(fields=["date", "employee"])]

    def __str__(self):
        return f"{self.employee} {self.date} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    @property
    def start_dt(self):
        return timezone.make_aware(datetime.combine(self.date, self.start_time))

    @property
    def end_dt(self):
        end = datetime.combine(self.date, self.end_time)
        if self.end_time <= self.start_time:          # overnight shift
            end += timedelta(days=1)
        return timezone.make_aware(end)


class Attendance(models.Model):
    class Status:
        ON_TIME = "on_time"
        LATE = "late"
        ABSENT = "absent"
        UNSCHEDULED = "unscheduled"

    GRACE_MINUTES = 10

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name="attendances")
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL)
    clock_in = models.DateTimeField(auto_now_add=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=15, default="PRESENT")
    notes = models.CharField(max_length=200, blank=True, default="")

    def __str__(self):
        return f"{self.employee} in {self.clock_in:%Y-%m-%d %H:%M}"

    @property
    def hours_worked(self):
        end = self.clock_out or timezone.now()
        return round((end - self.clock_in).total_seconds() / 3600, 2)

    @classmethod
    def punch_in(cls, employee):
        """Clock in; links to today's shift and flags lateness."""
        if cls.objects.filter(employee=employee, clock_out__isnull=True).exists():
            raise ValueError("Employee is already clocked in.")
        now = timezone.now()
        shift = employee.shifts.filter(date=timezone.localdate(now)).order_by("start_time").first()
        status = cls.Status.UNSCHEDULED
        if shift:
            late_after = shift.start_dt + timedelta(minutes=cls.GRACE_MINUTES)
            status = cls.Status.LATE if now > late_after else cls.Status.ON_TIME
        return cls.objects.create(employee=employee, shift=shift, clock_in=now, status=status)

    def punch_out(self):
        if self.clock_out:
            raise ValueError("Already clocked out.")
        self.clock_out = timezone.now()
        self.save(update_fields=["clock_out"])
        return self

class TemporaryAccess(models.Model):
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='temp_accesses')
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='granted_temp_accesses')
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_valid(self):
        return timezone.now() < self.expires_at

    def __str__(self):
        return f"{self.employee.username} - {self.permission.name} (Valid until {self.expires_at})"

class StoreSettings(models.Model):
    """Singleton row holding the store-wide configuration used by the UI.

    Rates are stored as fractions (0.12 = 12%); the settings screen edits them
    as percentages. Access the one row with ``StoreSettings.load()``.
    """

    # Store profile
    store_name = models.CharField(max_length=120, default="Fake Arab Shawarma Station")
    branch_name = models.CharField(max_length=120, blank=True)
    address = models.CharField(max_length=255, blank=True)
    contact_number = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)

    # Operations
    opening_time = models.TimeField(default=time(10, 0))
    closing_time = models.TimeField(default=time(22, 0))
    currency_symbol = models.CharField(max_length=8, default="₱")

    # Sales & tax
    vat_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.12"),
        help_text="Stored as a fraction (0.12 = 12%).",
    )
    service_charge_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0"),
        help_text="Stored as a fraction (0.05 = 5%).",
    )

    # Receipt
    receipt_footer = models.CharField(max_length=200, blank=True, default="Thank you! Come again.")
    show_vat_on_receipt = models.BooleanField(default=True)

    # Inventory & kitchen
    block_sales_on_insufficient_stock = models.BooleanField(default=False)
    kds_target_minutes = models.PositiveSmallIntegerField(default=10)

    # Security
    manager_override_pin = models.CharField(max_length=12, default="1234")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store Settings"
        verbose_name_plural = "Store Settings"

    def __str__(self):
        return self.store_name

    def save(self, *args, **kwargs):
        # There is only ever one settings row.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def vat_percent(self):
        return (self.vat_rate or Decimal("0")) * 100

    @property
    def service_charge_percent(self):
        return (self.service_charge_rate or Decimal("0")) * 100
