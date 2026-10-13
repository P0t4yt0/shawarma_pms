from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Ingredient, LowStockAlert

@login_required
def inventory_list(request):
    # Kukunin ang lahat ng active na ingredients, naka-sort by category tapos name
    ingredients = Ingredient.objects.filter(is_active=True).order_by('category', 'name')
    
    # Kukunin ang mga active na low stock alerts
    active_alerts = LowStockAlert.objects.filter(status=LowStockAlert.Status.OPEN).select_related('ingredient')

    context = {
        'ingredients': ingredients,
        'alerts': active_alerts,
    }
    return render(request, 'inventory_list.html', context)

def product_management(request):
    return render(request, 'product_management.html')