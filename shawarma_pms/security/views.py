from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from .models import TemporaryAccess, EmployeeProfile, Attendance
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from .models import Attendance, EmployeeProfile
from activity_log.services import log_activity
from django.contrib.auth.decorators import login_required
from .models import Attendance

from django.contrib.auth.models import User, Permission
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from .models import TemporaryAccess
from .decorators import require_dynamic_access

from django.contrib.auth.models import Group
from .models import Attendance
import random

from django.core.exceptions import PermissionDenied
from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from .models import Shift, Role, StoreSettings

@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("security:home")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is None:
            messages.error(request, "Invalid username or password.")
            return render(request, "login.html")

        employee = getattr(user, "employee", None)

        if employee is None and not user.is_staff:
            messages.error(
                request,
                "Your account is not linked to an employee profile.",
            )
            return render(request, "login.html")

        if employee is not None and not employee.is_active_employee:
            messages.error(
                request,
                "Your employee account is inactive. Please contact your manager.",
            )
            return render(request, "login.html")

        auth_login(request, user)

        if request.POST.get("remember_me") == "on":
            request.session.set_expiry(1209600)
        else:
            request.session.set_expiry(0)

        return redirect("security:home")

    return render(request, "login.html")


@login_required
@require_POST
def clock_in(request):
    try:
        rec = Attendance.punch_in(request.user.employee)
    except (ValueError, AttributeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    log_activity(
        request.user,
        "clock_in",
        "security",
        rec,
        f"Clocked in ({rec.status})",
    )

    return JsonResponse({
        "id": rec.id,
        "status": rec.status,
        "clock_in": rec.clock_in.isoformat(),
    })


@login_required
@require_POST
def clock_out(request):
    rec = Attendance.objects.filter(
        employee__user=request.user,
        clock_out__isnull=True,
    ).first()

    if not rec:
        return JsonResponse({"error": "Not clocked in."}, status=400)

    rec.punch_out()

    log_activity(
        request.user,
        "clock_out",
        "security",
        rec,
        f"Clocked out ({rec.hours_worked} h)",
    )

    return JsonResponse({
        "id": rec.id,
        "hours_worked": rec.hours_worked,
    })

def forgot_password(request):
    return render(request, "forgot_password.html", {
        "step": "request",
    })

def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("security:login")

@login_required
def dashboard_view(request):
    """Dashboard + Smart Promotion Recommendation banner (managers only; never breaks the page if it fails)."""
    import logging
    from .templatetags.access_tags import has_dynamic_access
    can_report = has_dynamic_access(request.user, "reports_analytics.view_demandforecast")
    can_create = has_dynamic_access(request.user, "promotions.add_promotion")
    ctx = {"show_promo_banner": False, "can_view_promo_report": can_report, "can_create_promo": can_create}
    if can_report or can_create:
        try:
            from reports_analytics.recommendations import get_cached_recommendations
            ctx["promo_rec"] = get_cached_recommendations()
            ctx["show_promo_banner"] = True
        except Exception:
            logging.getLogger(__name__).exception("Promotion recommendations failed; banner hidden.")
    return render(request, "dashboard.html", ctx)

@require_dynamic_access("security.manage_employees")
def user_roles_view(request):
    if request.method == 'POST':
        if 'grant_access' in request.POST:
            user_id = request.POST.get('employee_id')
            perm_codenames = request.POST.getlist('permissions') # Binago: getlist para makuha lahat ng checked
            duration_hours = int(request.POST.get('duration'))
            
            if not perm_codenames:
                messages.error(request, "Please select at least one module.")
                return redirect('security:user_roles')
                
            employee = User.objects.get(id=user_id)
            expires_at = timezone.now() + timedelta(hours=duration_hours)
            
            # I-loop ang bawat na-check na permission at gawan ng access
            for codename in perm_codenames:
                permission = Permission.objects.get(codename=codename)
                TemporaryAccess.objects.create(
                    employee=employee,
                    permission=permission,
                    granted_by=request.user,
                    expires_at=expires_at
                )
                
            messages.success(request, f"Temporary access granted to {employee.first_name} for {duration_hours} hours.")
            return redirect('security:user_roles')
            
        elif 'revoke_access' in request.POST:
            access_id = request.POST.get('access_id')
            TemporaryAccess.objects.filter(id=access_id).delete()
            messages.success(request, "Access revoked successfully.")
            return redirect('security:user_roles')

    # Kunin lang ang mga regular employees (Hindi superuser/admin)
    employees = User.objects.filter(is_superuser=False, is_active=True)
    
    # Ito yung mga specific modules/tabs na pwedeng ipa-unlock ng admin
    # Kunin ang LAAHAT ng permissions na naka-map sa sidebar tabs
    available_permissions = Permission.objects.filter(
        codename__in=[
            # POS & Orders
            'add_order', 'view_order', 'view_kds', 'view_orderstatuslog', 'view_receipt',
            # Products & Inventory
            'view_menuitem', 'view_category', 'view_ingredient', 'view_stockmovement', 'view_recipe', 'adjust_stock',
            # Promotions & Discounts
            'view_promotion', 'view_applieddiscount',
            # Reports & Analytics
            'view_dailysalessummary', 'view_variantdailysales', 'view_ingredientdailyusage', 'view_demandforecast', 'view_lowstockalert',
            # Employees & Settings
            'manage_employees', 'view_attendance', 'view_shift', 'view_activitylog'
        ]
    )
    
    # I-grupo sila nang maayos para sa UI
    grouped_permissions = {
        'POS & Orders': available_permissions.filter(codename__in=['add_order', 'view_order', 'view_kds', 'view_orderstatuslog', 'view_receipt']),
        'Products & Inventory': available_permissions.filter(codename__in=['view_menuitem', 'view_category', 'view_ingredient', 'view_stockmovement', 'view_recipe', 'adjust_stock']),
        'Promos & Discounts': available_permissions.filter(codename__in=['view_promotion', 'view_applieddiscount']),
        'Reports': available_permissions.filter(codename__in=['view_dailysalessummary', 'view_variantdailysales', 'view_ingredientdailyusage', 'view_demandforecast', 'view_lowstockalert']),
        'Employees & Settings': available_permissions.filter(codename__in=['manage_employees', 'view_attendance', 'view_shift', 'view_activitylog']),
    }
    
    active_accesses = TemporaryAccess.objects.filter(expires_at__gt=timezone.now()).order_by('-created_at')
    
    # I-update ang context para gamitin ang grouped_permissions
    context = {
        'employees': employees,
        'grouped_permissions': grouped_permissions,
        'active_accesses': active_accesses
    }
    return render(request, 'user_roles.html', context)
    

@require_dynamic_access("security.manage_employees")
def employee_list_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_employee':
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            username = request.POST.get('username')
            password = request.POST.get('password')
            
            # 1. FORCE LOWERCASE AGAD para iwas KeyError sa models.py!
            raw_role = request.POST.get('role', 'cashier')
            role_input = str(raw_role).lower()  # Siguradong 'cashier', 'kitchen', or 'manager'
            
            phone = request.POST.get('phone', 'N/A')
            
            if User.objects.filter(username=username).exists():
                messages.error(request, "Username already exists! Please choose another.")
                return redirect('security:employee_list')
                
            # 2. Gumawa ng Django User Account
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_staff=True  
            )
            
            # 3. Gumawa ng Employee Profile 
            # (Hahayaan na natin ang models.py mo na mag-assign ng Group via sync_group)
            from .models import EmployeeProfile
            import random
            from django.utils import timezone
            
            emp_id = f"EMP-{random.randint(10000, 99999)}"
            EmployeeProfile.objects.create(
                user=user,
                employee_id=emp_id,
                role=role_input,  # <--- Ipapasa natin ang sureball na lowercase
                phone=phone,
                hire_date=timezone.now().date(),
                hourly_rate=0.00,
                is_active_employee=True
            )
            
            messages.success(request, f"Employee {first_name} successfully added.")
            return redirect('security:employee_list')

        elif action == 'toggle_status':
            user_id = request.POST.get('user_id')
            emp = User.objects.get(id=user_id)
            emp.is_active = not emp.is_active
            emp.save()
            
            if hasattr(emp, 'employee'):
                emp.employee.is_active_employee = emp.is_active
                emp.employee.save()
                
            status_msg = "reactivated" if emp.is_active else "deactivated"
            messages.success(request, f"Account of {emp.first_name} has been {status_msg}.")
            return redirect('security:employee_list')

        # Idagdag itong bagong block pagkatapos ng toggle_status block
        elif action == 'delete_employee':
            user_id = request.POST.get('user_id')
            user_to_delete = User.objects.get(id=user_id)
            username = user_to_delete.username
            user_to_delete.delete()  # Automatic na buburahin pati yung Profile nito
            
            messages.success(request, f"Account @{username} has been permanently deleted.")
            return redirect('security:employee_list')

    # Kunin lahat ng staff (hindi kasama ang superuser)
    employees = User.objects.filter(is_superuser=False).select_related('employee').order_by('-date_joined')
    
    return render(request, 'employee_list.html', {'employees': employees})

@login_required
def attendance_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'kiosk_punch':
            # Kunin ang credentials mula sa popup
            u = request.POST.get('kiosk_username')
            p = request.POST.get('kiosk_password')
            
            # I-verify ang account sa background (hindi ito maglo-logout sa current POS session)
            staff_user = authenticate(username=u, password=p)
            
            if staff_user and hasattr(staff_user, 'employee'):
                emp_profile = staff_user.employee
                
                # Check kung may open na shift
                active_log = Attendance.objects.filter(employee=emp_profile, clock_out__isnull=True).first()
                
                if not active_log:
                    # CLOCK IN
                    Attendance.objects.create(
                        employee=emp_profile,
                        clock_in=timezone.now(),
                        status='PRESENT'
                    )
                    messages.success(request, f"Welcome, {staff_user.first_name}! You are CLOCKED IN.")
                else:
                    # CLOCK OUT
                    active_log.clock_out = timezone.now()
                    active_log.save()
                    messages.success(request, f"Goodbye, {staff_user.first_name}! You are CLOCKED OUT.")
            else:
                messages.error(request, "Invalid username or password. Please try again.")
                
        return redirect('security:attendance')

    is_manager = request.user.is_superuser or request.user.has_perm('security.manage_employees')
    
    if is_manager:
        # Manager: Makita ang huling 50 logs ng lahat
        logs = Attendance.objects.all().order_by('-clock_in')[:50]
    else:
        # Cashier/Kitchen Main Accounts: Makita lang ang logs para sa araw na ito (para ma-check ng staff kung pumasok ang punch nila)
        today = timezone.now().date()
        logs = Attendance.objects.filter(clock_in__date=today).order_by('-clock_in')

    return render(request, 'attendance.html', {
        'logs': logs,
        'is_manager': is_manager
    })

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required

@login_required
def account_security_view(request):
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        # 1. I-check kung tama ang current password
        if not request.user.check_password(old_password):
            messages.error(request, "Incorrect current password.")
        # 2. I-check kung match ang bagong password
        elif new_password != confirm_password:
            messages.error(request, "New passwords do not match.")
        # 3. I-check ang minimum length para secure
        elif len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters long.")
        else:
            # I-save ang bagong password
            request.user.set_password(new_password)
            request.user.save()
            
            # I-update ang session para hindi ma-logout ang user
            update_session_auth_hash(request, request.user)
            messages.success(request, "Your password has been updated successfully!")
            
        return redirect('security:account_security')

    return render(request, 'account_security.html')


@login_required
@require_dynamic_access("security.view_shift")
def work_schedules_view(request):
    """Work Schedules: plan employee shifts (the `Shift` model).

    Read-only for users with `security.view_shift`; creating and removing
    shifts additionally requires `security.manage_employees`.
    """
    can_manage = request.user.is_superuser or request.user.has_perm("security.manage_employees")

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("You do not have permission to manage work schedules.")

        action = request.POST.get("action")

        if action == "add_shift":
            employee = EmployeeProfile.objects.filter(
                id=request.POST.get("employee")
            ).select_related("user").first()

            date_raw = (request.POST.get("date") or "").strip()
            start_raw = (request.POST.get("start_time") or "").strip()
            end_raw = (request.POST.get("end_time") or "").strip()
            role = (request.POST.get("role") or "").strip()
            notes = (request.POST.get("notes") or "").strip()

            errors = []
            if employee is None:
                errors.append("Please choose a valid employee.")
            if not date_raw:
                errors.append("Please choose a shift date.")
            if not start_raw or not end_raw:
                errors.append("Please provide both a start and end time.")
            elif start_raw == end_raw:
                errors.append("Start and end time cannot be the same.")

            shift_date = start_time = end_time = None
            if not errors:
                try:
                    shift_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
                    start_time = datetime.strptime(start_raw, "%H:%M").time()
                    end_time = datetime.strptime(end_raw, "%H:%M").time()
                except ValueError:
                    errors.append("Invalid date or time value.")

            # Block overlapping shifts for the same employee on the same day.
            if not errors and employee is not None:
                for existing in Shift.objects.filter(employee=employee, date=shift_date):
                    if start_time < existing.end_time and existing.start_time < end_time:
                        errors.append(
                            f"{employee} already has an overlapping shift on "
                            f"{shift_date:%b %d, %Y}."
                        )
                        break

            if errors:
                for error in errors:
                    messages.error(request, error)
                return redirect("security:work_schedules")

            valid_roles = {value for value, _label in Role.choices}
            if role not in valid_roles:
                role = employee.role

            shift = Shift.objects.create(
                employee=employee,
                date=shift_date,
                start_time=start_time,
                end_time=end_time,
                role=role,
                notes=notes,
                created_by=request.user,
            )
            log_activity(
                request.user, "create", "security", shift,
                f"Scheduled {employee} on {shift_date:%b %d, %Y} "
                f"{start_time:%H:%M}-{end_time:%H:%M}",
            )
            messages.success(request, f"Shift scheduled for {employee}.")
            return redirect("security:work_schedules")

        if action == "delete_shift":
            shift = Shift.objects.filter(
                id=request.POST.get("shift_id")
            ).select_related("employee__user").first()

            if shift is None:
                messages.error(request, "That shift could not be found.")
            else:
                label = str(shift)
                shift.delete()
                log_activity(request.user, "delete", "security", None, f"Removed shift: {label}")
                messages.success(request, "Shift removed from the schedule.")
            return redirect("security:work_schedules")

        return redirect("security:work_schedules")

    # ----- GET: filters + list -----
    period = request.GET.get("period", "this_week")
    employee_filter = request.GET.get("employee", "")

    today = timezone.localdate()
    if period == "today":
        date_from = date_to = today
    elif period == "next_7":
        date_from = today
        date_to = today + timedelta(days=6)
    elif period == "all":
        date_from = date_to = None
    else:
        period = "this_week"
        date_from = today - timedelta(days=today.weekday())
        date_to = date_from + timedelta(days=6)

    shifts_qs = Shift.objects.select_related("employee__user", "created_by")
    if date_from and date_to:
        shifts_qs = shifts_qs.filter(date__gte=date_from, date__lte=date_to)
    if employee_filter:
        shifts_qs = shifts_qs.filter(employee_id=employee_filter)

    shifts = list(shifts_qs.order_by("date", "start_time"))

    total_hours = 0.0
    for shift in shifts:
        start = datetime.combine(shift.date, shift.start_time)
        end = datetime.combine(shift.date, shift.end_time)
        if shift.end_time <= shift.start_time:      # overnight shift
            end += timedelta(days=1)
        shift.duration_hours = round((end - start).total_seconds() / 3600, 1)
        total_hours += shift.duration_hours

    stats = {
        "total_shifts": len(shifts),
        "today_shifts": sum(1 for s in shifts if s.date == today),
        "employees": len({s.employee_id for s in shifts}),
        "hours": round(total_hours, 1),
    }

    employees = (
        EmployeeProfile.objects.filter(is_active_employee=True)
        .select_related("user")
        .order_by("employee_id")
    )

    return render(request, "work_schedules.html", {
        "shifts": shifts,
        "stats": stats,
        "employees": employees,
        "period": period,
        "employee_filter": employee_filter,
        "date_from": date_from,
        "date_to": date_to,
        "today": today,
        "can_manage": can_manage,
        "role_choices": Role.choices,
    })


@login_required
@require_dynamic_access("security.manage_employees")
def general_settings_view(request):
    """General Settings: store profile, operating hours, tax, receipts,
    inventory/kitchen behaviour and the manager override PIN."""
    store = StoreSettings.load()

    if request.method == "POST":
        errors = []

        store_name = (request.POST.get("store_name") or "").strip()
        branch_name = (request.POST.get("branch_name") or "").strip()
        address = (request.POST.get("address") or "").strip()
        contact_number = (request.POST.get("contact_number") or "").strip()
        email = (request.POST.get("email") or "").strip()
        currency_symbol = (request.POST.get("currency_symbol") or "").strip()
        receipt_footer = (request.POST.get("receipt_footer") or "").strip()
        manager_pin = (request.POST.get("manager_override_pin") or "").strip()

        if not store_name:
            errors.append("Store name is required.")
        if not currency_symbol:
            errors.append("Currency symbol is required.")

        for value, limit, label in (
            (store_name, 120, "Store name"),
            (branch_name, 120, "Branch name"),
            (address, 255, "Address"),
            (contact_number, 40, "Contact number"),
            (currency_symbol, 8, "Currency symbol"),
            (receipt_footer, 200, "Receipt footer"),
        ):
            if len(value) > limit:
                errors.append(f"{label} must be {limit} characters or fewer.")

        if email:
            if len(email) > 254:
                errors.append("Email address is too long.")
            else:
                try:
                    validate_email(email)
                except ValidationError:
                    errors.append("Please enter a valid email address.")

        if not manager_pin.isdigit() or not (4 <= len(manager_pin) <= 12):
            errors.append("Manager override PIN must be 4-12 digits.")

        def parse_time(field, label):
            raw = (request.POST.get(field) or "").strip()
            try:
                return datetime.strptime(raw, "%H:%M").time()
            except (TypeError, ValueError):
                errors.append(f"{label} is invalid.")
                return None

        opening_time = parse_time("opening_time", "Opening time")
        closing_time = parse_time("closing_time", "Closing time")

        def parse_percent(field, label):
            raw = (request.POST.get(field) or "").strip()
            try:
                value = Decimal(raw)
            except (InvalidOperation, ValueError):
                errors.append(f"{label} must be a number.")
                return None
            if value < 0 or value > 100:
                errors.append(f"{label} must be between 0 and 100.")
                return None
            return (value / Decimal("100")).quantize(Decimal("0.0001"))

        vat_rate = parse_percent("vat_percent", "VAT rate")
        service_charge_rate = parse_percent("service_charge_percent", "Service charge")

        try:
            kds_target_minutes = int(request.POST.get("kds_target_minutes") or 0)
        except (TypeError, ValueError):
            kds_target_minutes = 0
        if not (1 <= kds_target_minutes <= 120):
            errors.append("Kitchen target time must be between 1 and 120 minutes.")

        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect("security:general_settings")

        store.store_name = store_name
        store.branch_name = branch_name
        store.address = address
        store.contact_number = contact_number
        store.email = email
        store.opening_time = opening_time
        store.closing_time = closing_time
        store.currency_symbol = currency_symbol
        store.vat_rate = vat_rate
        store.service_charge_rate = service_charge_rate
        store.receipt_footer = receipt_footer
        store.show_vat_on_receipt = request.POST.get("show_vat_on_receipt") == "on"
        store.block_sales_on_insufficient_stock = (
            request.POST.get("block_sales_on_insufficient_stock") == "on"
        )
        store.kds_target_minutes = kds_target_minutes
        store.manager_override_pin = manager_pin
        store.save()

        log_activity(request.user, "update", "security", store, "Updated general settings")
        messages.success(request, "General settings saved successfully.")
        return redirect("security:general_settings")

    return render(request, "general_settings.html", {"store": store})

from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required

from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required

@login_required
def api_notifications(request):
    notifications = []
    
    # Local imports para iwas circular error
    from inventory.models import Ingredient
    from point_of_sale.models import Order
    
    # Kunin ang mismong role ng naka-login na user
    role = 'admin'
    if hasattr(request.user, 'employee'):
        role = request.user.employee.role.lower()
    elif request.user.is_superuser:
        role = 'admin'

    # 1. Low Stock Alerts (Makikita at tutunog sa MANAGER, ADMIN, at KITCHEN)
    if role in ['manager', 'admin', 'kitchen']:
        low_stock_items = Ingredient.objects.filter(is_active=True)
        for item in low_stock_items:
            if item.current_stock <= item.reorder_level:
                notifications.append({
                    'id': f"stock_{item.id}_{item.updated_at.strftime('%Y%m%d')}",
                    'type': 'warning',
                    'icon': 'ph-warning-circle',
                    'title': 'Low Stock Alert',
                    'message': f"{item.name} is running low ({item.current_stock} {item.unit} left).",
                    'time': item.updated_at.strftime("%I:%M %p"),
                    'timestamp': item.updated_at.timestamp(),
                    'link': reverse('inventory:list')
                })

    # 2. New Orders (Makikita at tutunog STRICTLY sa KITCHEN lang)
    if role == 'kitchen':
        pending_orders = Order.objects.filter(status='pending').order_by('-created_at')[:5]
        for order in pending_orders:
            notifications.append({
                'id': f"order_pending_{order.id}",
                'type': 'info',
                'icon': 'ph-cooking-pot',
                'title': 'New Order Received',
                'message': f"Order #{order.order_number} is waiting to be prepared.",
                'time': order.created_at.strftime("%I:%M %p"),
                'timestamp': order.created_at.timestamp(),
                'link': reverse('pos:kds_screen')
            })

    # 3. Ready Orders (Makikita at tutunog STRICTLY sa CASHIER lang)
    if role == 'cashier':
        ready_orders = Order.objects.filter(status='ready_to_serve').order_by('-last_status_at')[:5]
        for order in ready_orders:
            notifications.append({
                'id': f"order_ready_{order.id}",
                'type': 'success',
                'icon': 'ph-bell-ringing',
                'title': 'Order Ready',
                'message': f"Order #{order.order_number} is ready for pickup/dispatch.",
                'time': order.last_status_at.strftime("%I:%M %p"),
                'timestamp': order.last_status_at.timestamp(),
                'link': reverse('pos:order_list')
            })

    # I-sort para pinakabago ang nasa itaas
    notifications.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return JsonResponse({'notifications': notifications[:15]})
