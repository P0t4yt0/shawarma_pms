"""Role-based access helpers built ONLY on Django permissions/groups."""
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

@login_required
def role_home(request):
    """Kitchen staff -> KDS, cashiers -> POS, managers/admin -> Dashboard."""
    user = request.user
    
    # 1. Kung Superuser o Manager (may manage_employees permission), idiretso sa Dashboard
    if user.is_superuser or user.has_perm("security.manage_employees"):
        return redirect("security:dashboard")
        
    # 2. Kung Kitchen staff (may KDS view pero bawal mag-order)
    if user.has_perm("point_of_sale.view_kds") and not user.has_perm("point_of_sale.add_order"):
        return redirect("pos:kds_screen")
        
    # 3. Kung Cashier (may add order permission pero hindi manager)
    if user.has_perm("point_of_sale.add_order"):
        return redirect("pos:pos_screen")
        
    # Fallback kung walang malinaw na role
    return redirect("security:dashboard")

class PermissionRequired(PermissionRequiredMixin):
    """Class-based view mixin: raise 403 (not redirect loop) for logged-in users lacking permission."""
    raise_exception = True