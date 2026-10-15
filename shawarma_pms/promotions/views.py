import json
from decimal import Decimal
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db import transaction

from .models import Promotion, AppliedDiscount
from point_of_sale.models import Category, MenuVariant

@login_required
def promotions_list(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'add':
                with transaction.atomic():
                    promo = Promotion.objects.create(
                        name=data['name'],
                        code=data.get('code', ''),
                        campaign_type=data['campaign_type'],
                        kind=data['kind'],
                        value=Decimal(str(data['value'])),
                        scope=data['scope'],
                        start_date=data.get('start_date') if data.get('start_date') else None,
                        end_date=data.get('end_date') if data.get('end_date') else None,
                        is_active=True
                    )
                    
                    if data['scope'] == Promotion.Scope.CATEGORIES and data.get('target_id'):
                        promo.categories.add(data['target_id'])
                    elif data['scope'] == Promotion.Scope.VARIANTS and data.get('target_id'):
                        promo.variants.add(data['target_id'])
                        
                return JsonResponse({"success": True, "message": "Promotion created successfully!"})
                
            elif action == 'toggle_status':
                promo = Promotion.objects.get(id=data['promo_id'])
                promo.is_active = not promo.is_active
                promo.save(update_fields=['is_active'])
                return JsonResponse({"success": True, "message": "Promotion status updated!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    # Kukunin ang mga existing promos
    promos = Promotion.objects.all().order_by('-created_at')
    
    # Ipasa ang categories at variants para sa dropdown choices sa UI
    categories = list(Category.objects.filter(is_active=True).values('id', 'name'))
    variants = list(MenuVariant.objects.filter(is_active=True).values('id', 'name', 'item__name'))
    
    return render(request, 'promotions.html', {
        'promos': promos,
        'categories_json': json.dumps(categories),
        'variants_json': json.dumps(variants)
    })

@login_required
def promotion_history(request):
    # Kunin lahat ng log kung saan ginamit ang discount/promo
    history_logs = AppliedDiscount.objects.select_related('order', 'promotion', 'applied_by').order_by('-created_at')
    
    return render(request, 'promotion_history.html', {
        'history_logs': history_logs
    })