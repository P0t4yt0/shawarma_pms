from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from activity_log.services import log_activity
from .models import Attendance


@login_required
@require_POST
def clock_in(request):
    try:
        rec = Attendance.punch_in(request.user.employee)
    except (ValueError, AttributeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    log_activity(request.user, "clock_in", "security", rec, f"Clocked in ({rec.status})")
    return JsonResponse({"id": rec.id, "status": rec.status, "clock_in": rec.clock_in.isoformat()})


@login_required
@require_POST
def clock_out(request):
    rec = Attendance.objects.filter(employee__user=request.user, clock_out__isnull=True).first()
    if not rec:
        return JsonResponse({"error": "Not clocked in."}, status=400)
    rec.punch_out()
    log_activity(request.user, "clock_out", "security", rec, f"Clocked out ({rec.hours_worked} h)")
    return JsonResponse({"id": rec.id, "hours_worked": rec.hours_worked})
