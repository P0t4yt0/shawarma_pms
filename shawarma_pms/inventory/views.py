import json
from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.utils.text import slugify
from django.contrib.auth.decorators import login_required
from decimal import Decimal
from django.core.paginator import Paginator

from .models import Ingredient, LowStockAlert, Ingredient, StockMovement, Unit, Recipe, RecipeLine
from point_of_sale.models import Category, MenuItem, MenuVariant
from inventory.models import Ingredient, StockMovement, Unit, RecipeItem
from inventory.models import Category as InvCategory
from point_of_sale.models import Category as POSCategory, MenuVariant

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
                    ingredient.category_id = data['category_id'] # Direktang ID
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

    # Kukunin lahat ng ingredients at ang POS categories
    ingredient_queryset = Ingredient.objects.select_related('category').order_by('name')
    categories = Category.objects.filter(is_active=True).order_by('name')
    
    # Pagination Setup
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(ingredient_queryset, per_page)
    page_number = request.GET.get('page')
    ingredients = paginator.get_page(page_number)
    
    return render(request, 'inventory_list.html', {
        'ingredients': ingredients,
        'categories': categories,
    })

@login_required
def product_management(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'add':
                with transaction.atomic():
                    category = Category.objects.get(id=data['category_id'])
                    
                    # Hanapin kung may existing nang item name (ex. "Beef Shawarma"), kung wala, gawa bago
                    item, created = MenuItem.objects.get_or_create(
                        name=data['item_name'],
                        defaults={'category': category, 'is_active': True}
                    )
                    
                    # Gumawa ng specific variant (ex. "Standard" o "Large")
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
                    
                    # Update Variant details
                    variant.name = data['variant_name']
                    variant.sku = data['sku']
                    variant.price = data['price']
                    variant.is_active = data['is_active']
                    variant.save()
                    
                    # Update Item parent details
                    item = variant.item
                    item.name = data['item_name']
                    item.category_id = data['category_id']
                    item.save()
                    
                return JsonResponse({"success": True, "message": "Product updated successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    # GET Request: I-load ang Categories at MenuVariants para sa UI
    categories = Category.objects.filter(is_active=True)
    variant_queryset = MenuVariant.objects.select_related('item__category').order_by('item__category__name', 'item__name')
    
    # Pagination Setup
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(variant_queryset, per_page)
    page_number = request.GET.get('page')
    variants = paginator.get_page(page_number)
    
    return render(request, 'product_management.html', {
        'categories': categories,
        'variants': variants
    })

@login_required
def category_management(request):
    # Alamin kung anong tab ang naka-open (default ay 'pos')
    active_tab = request.GET.get('tab', 'pos')

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')
            target_tab = data.get('tab', 'pos')
            
            # Piliin kung aling Model ang gagamitin depende sa tab
            TargetCategoryModel = POSCategory if target_tab == 'pos' else InvCategory

            if action == 'add':
                with transaction.atomic():
                    name = data['name'].strip()
                    slug = slugify(name)
                    TargetCategoryModel.objects.create(name=name, slug=slug, is_active=True)
                return JsonResponse({"success": True, "message": "Category added successfully!"})
                
            elif action == 'edit':
                with transaction.atomic():
                    category = TargetCategoryModel.objects.get(id=data['category_id'])
                    category.name = data['name'].strip()
                    category.slug = slugify(category.name)
                    category.is_active = data['is_active']
                    category.save()
                return JsonResponse({"success": True, "message": "Category updated successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    # GET Request: Kunin ang tamang categories base sa active tab
    if active_tab == 'pos':
        all_categories = POSCategory.objects.order_by('name')
        processed_categories = []
        for category in all_categories:
            variants = MenuVariant.objects.filter(item__category=category).select_related('item')
            variant_list = []
            for v in variants:
                variant_list.append({
                    'id': v.id,
                    'name': f"{v.item.name} ({v.name})",
                    'price': str(v.price),
                    'is_active': v.is_active,
                    'sku': v.sku if v.sku else f"PRD-{v.id}"
                })
            category.serializable_items = variant_list
            category.items_count = len(variant_list)
            processed_categories.append(category)
    else:
        # Para sa Inventory Categories, mga ingredients ang naka-link
        all_categories = InvCategory.objects.order_by('name')
        processed_categories = []
        for category in all_categories:
            ingredients = category.ingredients.all() # Gamit ang related_name na 'ingredients' sa models.py
            ingredient_list = []
            for ing in ingredients:
                ingredient_list.append({
                    'id': ing.id,
                    'name': f"{ing.name} ({ing.current_stock} {ing.unit})",
                    'is_active': ing.is_active,
                    'sku': ing.sku
                })
            category.serializable_items = ingredient_list
            category.items_count = len(ingredient_list)
            processed_categories.append(category)
        
    # Pagination Setup
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(processed_categories, per_page)
    page_number = request.GET.get('page')
    categories = paginator.get_page(page_number)
    
    return render(request, 'category_management.html', {
        'categories': categories,
        'active_tab': active_tab
    })

@login_required
def stock_movements_list(request):
    movements_qs = StockMovement.objects.select_related('ingredient', 'performed_by').order_by('-created_at')
    
    # Pagination Setup
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(movements_qs, per_page)
    page_number = request.GET.get('page')
    movements = paginator.get_page(page_number)
    
    return render(request, 'stock_movements.html', {
        'movements': movements,
    })

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
                    
                    # Update or Create para kung nandun na yung ingredient, i-uupdate na lang ang qty
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

    # GET Request: Kunin ang search query
    search_query = request.GET.get('search', '').strip()
    
    # Base queryset ng active variants
    variants_qs = MenuVariant.objects.select_related('item').filter(is_active=True)

    # I-apply ang Search Filter kung may tinype
    if search_query:
        variants_qs = variants_qs.filter(
            Q(item__name__icontains=search_query) | 
            Q(name__icontains=search_query)
        )
        
    variants_qs = variants_qs.order_by('item__name', 'name')

    # Pagination Setup (Halimbawa: 10 items per page)
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(variants_qs, per_page)
    page_number = request.GET.get('page')
    paginated_variants = paginator.get_page(page_number)

    ingredients = Ingredient.objects.filter(is_active=True).order_by('name')

    # I-convert yung PAGINATED items sa JSON format
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
            movement_type = data['movement_type'] # 'spoilage' o 'waste'
            note = data.get('note', '').strip()

            if qty <= 0:
                return JsonResponse({"success": False, "error": "Quantity must be greater than zero."})

            with transaction.atomic():
                # Awtomatikong magbabawas sa stock (kaya negative ang delta) at mag-re-record sa StockMovement
                ingredient.adjust(
                    delta=-qty,
                    movement_type=movement_type,
                    user=request.user,
                    note=note
                )
            return JsonResponse({"success": True, "message": "Loss reported successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    # Kukunin ang mga active ingredients para sa dropdown form
    ingredients = Ingredient.objects.filter(is_active=True).order_by('name')
    
    # Kukunin ang lahat ng spoilage at waste logs
    spoilage_qs = StockMovement.objects.filter(
        movement_type__in=[StockMovement.Type.SPOILAGE, StockMovement.Type.WASTE]
    ).select_related('ingredient', 'performed_by').order_by('-created_at')

    # Pagination Setup
    per_page = request.GET.get('per_page', 10)
    paginator = Paginator(spoilage_qs, per_page)
    page_number = request.GET.get('page')
    spoilage_logs = paginator.get_page(page_number)

    return render(request, 'spoilage_management.html', {
        'ingredients': ingredients,
        'spoilage_logs': spoilage_logs
    })