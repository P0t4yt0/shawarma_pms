
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from activity_log.services import log_activity
from .models import Attendance


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

        # Employees must have an active EmployeeProfile.
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

        # Remember Me keeps the session for 14 days.
        # Otherwise, the session expires when the browser closes.
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
