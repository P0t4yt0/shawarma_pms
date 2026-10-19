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
    return render(request, "dashboard.html")

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

