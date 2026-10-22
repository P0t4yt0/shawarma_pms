import json
from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.utils.text import slugify
from django.contrib.auth.decorators import login_required
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Q

# TAMA AT MALINIS NA IMPORTS
from inventory.models import Ingredient, LowStockAlert, StockMovement, Unit, Recipe, RecipeLine, RecipeItem
from inventory.models import Category as InvCategory

from point_of_sale.models import MenuItem, MenuVariant
from point_of_sale.models import Category as POSCategory


@login_required
def inventory_list(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'add':
                with transaction.atomic():
                    Ingredient.objects.create(
                        name=data['name'].strip(),
                        category_id=data['category_id'], 
                        unit=data['unit'],
                        reorder_level=data['reorder_level'],
                        cost_per_unit=data['cost_per_unit'],
                        is_active=True
                    )
                return JsonResponse({"success": True, "message": "Ingredient added successfully!"})
                
            elif action == 'edit':
                with transaction.atomic():
                    ingredient = Ingredient.objects.get(id=data['ingredient_id'])
                    ingredient.name = data['name'].strip()
                    ingredient.category_id = data['category_id']
                    ingredient.unit = data['unit']
                    ingredient.reorder_level = data['reorder_level']
                    ingredient.cost_per_unit = data['cost_per_unit']
                    ingredient.is_active = data['is_active']
                    ingredient.save()
                return JsonResponse({"success": True, "message": "Ingredient updated successfully!"})

            elif action == 'adjust':
                with transaction.atomic():
                    ingredient = Ingredient.objects.get(id=data['ingredient_id'])
                    delta = Decimal(str(data['delta']))
                    movement_type = data['movement_type']
                    note = data.get('note', '').strip()
                    
                    ingredient.adjust(delta, movement_type, user=request.user, note=note)
                    
                return JsonResponse({"success": True, "message": "Stock adjusted successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    search_query = request.GET.get('search', '').strip()
    category_filter = request.GET.get('category', '').strip()

    ingredient_queryset = Ingredient.objects.select_related('category').order_by('name')

    if search_query:
        ingredient_queryset = ingredient_queryset.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query)
        )

    if category_filter and category_filter != 'all':
        ingredient_queryset = ingredient_queryset.filter(category__name=category_filter)

    # DITO YUNG FIX: InvCategory ang ginamit para INGREDIENT categories lang ang lumabas!
    categories = InvCategory.objects.filter(is_active=True).order_by('name')
    
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(ingredient_queryset, per_page)
    page_number = request.GET.get('page')
    ingredients = paginator.get_page(page_number)
    
    return render(request, 'inventory_list.html', {
        'ingredients': ingredients,
        'categories': categories,
        'search_query': search_query,
        'category_filter': category_filter
    })


@login_required
def category_management(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')
            tab = data.get('tab', 'pos')

            # DYNAMIC SWITCHING: Aling table ang isasave?
            TargetCategory = POSCategory if tab == 'pos' else InvCategory

            if action == 'add':
                TargetCategory.objects.create(
                    name=data['name'].strip(),
                    is_active=True
                )
                return JsonResponse({"success": True, "message": "Category added successfully!"})
                
            elif action == 'edit':
                category = TargetCategory.objects.get(id=data['category_id'])
                category.name = data['name'].strip()
                category.is_active = data['is_active']
                category.save()
                return JsonResponse({"success": True, "message": "Category updated successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    active_tab = request.GET.get('tab', 'pos')
    TargetCategory = POSCategory if active_tab == 'pos' else InvCategory
    
    search_query = request.GET.get('search', '').strip()
    categories_qs = TargetCategory.objects.all().order_by('name')
    
    if search_query:
        categories_qs = categories_qs.filter(name__icontains=search_query)

    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(categories_qs, per_page)
    page_number = request.GET.get('page')
    categories = paginator.get_page(page_number)
    
    # Mag-inject ng count at items para sa "View" button ng UI
    for cat in categories:
        if active_tab == 'pos':
            related_items = cat.items.all()
            cat.items_count = related_items.count()
            cat.serializable_items = list(related_items.values('id', 'name', 'is_active'))
        else:
            related_ingredients = cat.ingredients.all()
            cat.items_count = related_ingredients.count()
            cat.serializable_items = list(related_ingredients.values('id', 'name', 'is_active'))
    
    return render(request, 'category_management.html', {
        'categories': categories,
        'active_tab': active_tab,
        'search_query': search_query
    })


@login_required
def product_management(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'add':
                with transaction.atomic():
                    # FIX: POSCategory ang gamit para sa menu items
                    category = POSCategory.objects.get(id=data['category_id'])
                    
                    item, created = MenuItem.objects.get_or_create(
                        name=data['item_name'],
                        defaults={'category': category, 'is_active': True}
                    )
                    
                    MenuVariant.objects.create(
                        item=item,
                        name=data['variant_name'],
                        sku=data['sku'],
                        price=data['price'],
                        is_active=True
                    )
                return JsonResponse({"success": True, "message": "Product added successfully!"})
                
            elif action == 'edit':
                with transaction.atomic():
                    variant = MenuVariant.objects.get(id=data['variant_id'])
                    variant.name = data['variant_name']
                    variant.sku = data['sku']
                    variant.price = data['price']
                    variant.is_active = data['is_active']
                    variant.save()
                    
                    item = variant.item
                    item.name = data['item_name']
                    item.category_id = data['category_id']
                    item.save()
                    
                return JsonResponse({"success": True, "message": "Product updated successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    # FIX: POSCategory para sa dropdown ng Menu
    categories = POSCategory.objects.filter(is_active=True)
    variant_queryset = MenuVariant.objects.select_related('item__category').order_by('item__category__name', 'item__name')
    
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(variant_queryset, per_page)
    page_number = request.GET.get('page')
    variants = paginator.get_page(page_number)
    
    return render(request, 'product_management.html', {
        'categories': categories,
        'variants': variants
    })


@login_required
def stock_movements_list(request):
    movements_qs = StockMovement.objects.select_related('ingredient', 'performed_by').order_by('-created_at')
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(movements_qs, per_page)
    page_number = request.GET.get('page')
    movements = paginator.get_page(page_number)
    return render(request, 'stock_movements.html', {'movements': movements})


@login_required
def recipe_management(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'add_item':
                with transaction.atomic():
                    variant = MenuVariant.objects.get(id=data['variant_id'])
                    ingredient = Ingredient.objects.get(id=data['ingredient_id'])
                    qty = Decimal(str(data['quantity']))
                    RecipeItem.objects.update_or_create(
                        variant=variant,
                        ingredient=ingredient,
                        defaults={'quantity_required': qty}
                    )
                return JsonResponse({"success": True, "message": "Ingredient added to recipe!"})

            elif action == 'remove_item':
                with transaction.atomic():
                    RecipeItem.objects.filter(id=data['recipe_item_id']).delete()
                return JsonResponse({"success": True, "message": "Ingredient removed from recipe."})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    search_query = request.GET.get('search', '').strip()
    variants_qs = MenuVariant.objects.select_related('item').filter(is_active=True)

    if search_query:
        variants_qs = variants_qs.filter(
            Q(item__name__icontains=search_query) | 
            Q(name__icontains=search_query)
        )
        
    variants_qs = variants_qs.order_by('item__name', 'name')
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(variants_qs, per_page)
    page_number = request.GET.get('page')
    paginated_variants = paginator.get_page(page_number)

    ingredients = Ingredient.objects.filter(is_active=True).order_by('name')

    variants_data = []
    for v in paginated_variants:
        recipe_items = v.recipe_items.select_related('ingredient').all()
        items_list = []
        total_cost = 0
        for r in recipe_items:
            cost = float(r.ingredient.cost_per_unit * r.quantity_required)
            total_cost += cost
            items_list.append({
                'id': r.id,
                'ingredient_id': r.ingredient.id,
                'ingredient_name': r.ingredient.name,
                'unit': r.ingredient.unit,
                'cost_per_unit': float(r.ingredient.cost_per_unit),
                'quantity_required': float(r.quantity_required),
                'cost': cost
            })

        variants_data.append({
            'id': v.id,
            'name': f"{v.item.name} ({v.name})",
            'price': float(v.price),
            'total_cost': total_cost,
            'recipe': items_list
        })

    return render(request, 'recipe_management.html', {
        'variants_json': json.dumps(variants_data),
        'ingredients': ingredients,
        'paginated_variants': paginated_variants,
        'search_query': search_query
    })


@login_required
def spoilage_management(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ingredient = Ingredient.objects.get(id=data['ingredient_id'])
            qty = Decimal(str(data['quantity']))
            movement_type = data['movement_type']
            note = data.get('note', '').strip()

            if qty <= 0:
                return JsonResponse({"success": False, "error": "Quantity must be greater than zero."})

            with transaction.atomic():
                ingredient.adjust(
                    delta=-qty,
                    movement_type=movement_type,
                    user=request.user,
                    note=note
                )
            return JsonResponse({"success": True, "message": "Loss reported successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    ingredients = Ingredient.objects.filter(is_active=True).order_by('name')
    spoilage_qs = StockMovement.objects.filter(
        movement_type__in=[StockMovement.Type.SPOILAGE, StockMovement.Type.WASTE]
    ).select_related('ingredient', 'performed_by').order_by('-created_at')

    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(spoilage_qs, per_page)
    page_number = request.GET.get('page')
    spoilage_logs = paginator.get_page(page_number)

    return render(request, 'spoilage_management.html', {
        'ingredients': ingredients,
        'spoilage_logs': spoilage_logs
    })