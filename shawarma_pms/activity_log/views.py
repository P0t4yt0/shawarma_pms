from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from security.decorators import require_dynamic_access
from .models import ActivityLog

@login_required
@require_dynamic_access("activity_log.view_activitylog")
def activity_log_list(request):
    logs = ActivityLog.objects.select_related('user').order_by('-created_at')[:300]
    
    # FIX: Kukunin lahat at gagamitan ng set() para matanggal ang duplicates, tapos iso-sort.
    raw_modules = ActivityLog.objects.exclude(module__isnull=True).values_list('module', flat=True)
    modules = sorted(list(set(raw_modules)))
    
    context = {
        'logs': logs,
        'modules': modules
    }
    return render(request, 'activity_logs.html', context)