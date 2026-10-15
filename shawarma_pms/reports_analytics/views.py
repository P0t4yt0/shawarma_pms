import json
from decimal import Decimal
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from point_of_sale.models import Order

@login_required
def sales_report(request):
    # Kukunin ang lahat ng completed o paid orders
    orders = Order.objects.select_related('cashier').prefetch_related('items', 'payments__method').exclude(status=Order.Status.CANCELLED).order_by('-created_at')
    
    total_sales = sum(o.total for o in orders)
    total_orders = orders.count()
    
    cash_sales = Decimal('0.00')
    ewallet_sales = Decimal('0.00')
    
    for o in orders:
        for p in o.payments.all():
            if p.method.kind == 'cash':
                cash_sales += p.amount
            else:
                ewallet_sales += p.amount

    return render(request, 'sales_report.html', {
        'orders': orders,
        'total_sales': total_sales,
        'total_orders': total_orders,
        'cash_sales': cash_sales,
        'ewallet_sales': ewallet_sales
    })