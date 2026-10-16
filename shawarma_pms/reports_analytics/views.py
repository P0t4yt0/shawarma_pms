import json
from decimal import Decimal
from datetime import timedelta

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F
from django.utils import timezone

from point_of_sale.models import Order, OrderItem
from inventory.models import Ingredient, StockMovement


@login_required
def sales_report(request):
    orders = Order.objects.select_related('cashier').prefetch_related(
        'items', 'payments__method'
    ).exclude(status=Order.Status.CANCELLED).order_by('-created_at')
    
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


@login_required
def product_sales_report(request):
    valid_items = OrderItem.objects.filter(
        order__status=Order.Status.COMPLETED
    ).exclude(order__status=Order.Status.CANCELLED)

    aggregated = valid_items.values(
        'variant__id',
        'variant__name',
        'variant__sku',
        'variant__price',
        'variant__item__name',
        'variant__item__category__name'
    ).annotate(
        total_quantity=Sum('quantity'),
        total_revenue=Sum(F('quantity') * F('unit_price') - F('discount_amount'))
    ).order_by('-total_quantity')

    product_sales = []
    total_units_sold = 0
    total_product_revenue = Decimal('0.00')
    top_product = "--"

    for entry in aggregated:
        qty = entry['total_quantity'] or 0
        rev = entry['total_revenue'] or Decimal('0.00')
        
        display_name = (
            f"{entry['variant__item__name']} ({entry['variant__name']})"
            if entry['variant__name']
            else entry['variant__item__name']
        )
        
        product_sales.append({
            'variant_name': display_name,
            'sku': entry['variant__sku'] or 'N/A',
            'category_name': entry['variant__item__category__name'] or 'Uncategorized',
            'total_quantity': qty,
            'unit_price': entry['variant__price'] or Decimal('0.00'),
            'total_revenue': rev
        })
        
        total_units_sold += qty
        total_product_revenue += rev

    if product_sales:
        top_product = product_sales[0]['variant_name']

    return render(request, 'product_sales.html', {
        'product_sales': product_sales,
        'total_units_sold': total_units_sold,
        'total_product_revenue': total_product_revenue,
        'top_product': top_product
    })


@login_required
def inventory_report(request):
    ingredients = Ingredient.objects.select_related('category').filter(is_active=True).order_by('name')
    
    total_items = ingredients.count()
    low_stock_count = 0
    out_of_stock_count = 0
    inventory_items = []

    for item in ingredients:
        if item.current_stock <= 0:
            out_of_stock_count += 1
            status = 'out'
        elif item.current_stock <= item.reorder_level:
            low_stock_count += 1
            status = 'low'
        else:
            status = 'sufficient'

        inventory_items.append({
            'sku': item.sku or f"SKU-{item.id}",
            'name': item.name,
            'category_name': item.category.name if item.category else 'Uncategorized',
            'current_stock': item.current_stock,
            'reorder_level': item.reorder_level,
            'unit': item.unit,
            'status': status
        })

    return render(request, 'inventory_reports.html', {
        'inventory_items': inventory_items,
        'total_items': total_items,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count
    })


@login_required
def sales_forecast_report(request):
    now = timezone.now()
    forecast_data = []
    weekly_totals = []

    for i in range(4, 0, -1):
        start_date = now - timedelta(days=i * 7)
        end_date = now - timedelta(days=(i - 1) * 7)
        
        week_orders = Order.objects.filter(
            status=Order.Status.COMPLETED,
            created_at__gte=start_date,
            created_at__lt=end_date
        )
        week_rev = sum(o.total for o in week_orders) or Decimal('0.00')
        weekly_totals.append(week_rev)

        forecast_data.append({
            'period': f"Week -{i}",
            'historical_avg': week_rev,
            'projected_sales': (week_rev * Decimal('1.05')).quantize(Decimal('0.01')),
            'growth_rate': 5.0
        })

    if weekly_totals:
        avg_rev = sum(weekly_totals) / len(weekly_totals)
    else:
        avg_rev = Decimal('0.00')

    forecast_data.append({
        'period': "Next Week (Forecast)",
        'historical_avg': avg_rev.quantize(Decimal('0.01')),
        'projected_sales': (avg_rev * Decimal('1.08')).quantize(Decimal('0.01')),
        'growth_rate': 8.0
    })

    return render(request, 'sales_forecast.html', {
        'forecast_data': forecast_data
    })


@login_required
def stock_spoilage_report(request):
    spoilage_movements = StockMovement.objects.filter(
        movement_type__in=[StockMovement.Type.SPOILAGE, StockMovement.Type.WASTE]
    ).select_related('ingredient', 'performed_by').order_by('-created_at')

    total_spoilage_cost = Decimal('0.00')
    items_spoiled_count = spoilage_movements.count()
    spoilage_records = []

    for m in spoilage_movements:
        qty_lost = abs(m.quantity_change)
        cost_loss = (qty_lost * m.ingredient.cost_per_unit).quantize(Decimal('0.01'))
        total_spoilage_cost += cost_loss

        spoilage_records.append({
            'date_recorded': m.created_at,
            'item_name': m.ingredient.name,
            'quantity': qty_lost,
            'unit': m.ingredient.unit,
            'type': m.get_movement_type_display(),
            'reason': m.note or 'N/A',
            'reported_by': m.performed_by.username if m.performed_by else 'Admin',
            'cost_loss': cost_loss
        })

    return render(request, 'stock_and_spoilage_reports.html', {
        'spoilage_records': spoilage_records,
        'total_spoilage_cost': total_spoilage_cost,
        'items_spoiled_count': items_spoiled_count
    })