"""Role-based access helpers built ONLY on Django permissions/groups."""
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

ROLE_HOME = {            # first matching permission decides the landing page after login
    "point_of_sale.view_kds": "pos:kds_screen",
    "point_of_sale.add_order": "pos:pos_screen",
    "reports_analytics.view_dailysalessummary": "pos:pos_screen",
}


@login_required
def role_home(request):
    """Kitchen staff -> KDS, cashiers -> POS, managers -> POS (admin/reports linked from there)."""
    user = request.user
    if user.has_perm("point_of_sale.view_kds") and not user.has_perm("point_of_sale.add_order"):
        return redirect("pos:kds_screen")
    if user.has_perm("point_of_sale.add_order"):
        return redirect("pos:pos_screen")
    return redirect("admin:index")


class PermissionRequired(PermissionRequiredMixin):
    """Class-based view mixin: raise 403 (not redirect loop) for logged-in users lacking permission."""
    raise_exception = True
