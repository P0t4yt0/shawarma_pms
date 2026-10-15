import json
from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.utils.text import slugify
from django.contrib.auth.decorators import login_required
from decimal import Decimal

from .models import Ingredient, LowStockAlert, Ingredient, StockMovement, Unit
from point_of_sale.models import Category, MenuItem, MenuVariant
from inventory.models import Ingredient, StockMovement, Unit, RecipeItem

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
    ingredients = Ingredient.objects.select_related('category').order_by('name')
    categories = Category.objects.filter(is_active=True).order_by('name')
    
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
    variants = MenuVariant.objects.select_related('item__category').order_by('item__category__name', 'item__name')
    
    return render(request, 'product_management.html', {
        'categories': categories,
        'variants': variants
    })

@login_required
def category_management(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'add':
                with transaction.atomic():
                    name = data['name'].strip()
                    slug = slugify(name)
                    Category.objects.create(name=name, slug=slug, is_active=True)
                return JsonResponse({"success": True, "message": "Category added successfully!"})
                
            elif action == 'edit':
                with transaction.atomic():
                    category = Category.objects.get(id=data['category_id'])
                    category.name = data['name'].strip()
                    category.slug = slugify(category.name)
                    category.is_active = data['is_active']
                    category.save()
                return JsonResponse({"success": True, "message": "Category updated successfully!"})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    # GET Request: Kukunin lahat ng categories
    categories = Category.objects.order_by('name')
    
    for category in categories:
        # IMBES NA ITEM, KUKUNIN NATIN ANG MISMONG VARIANTS PARA TAMA ANG STATUS
        variants = MenuVariant.objects.filter(item__category=category).select_related('item')
        
        variant_list = []
        for v in variants:
            variant_list.append({
                'id': v.id,
                'name': f"{v.item.name} ({v.name})", # Pinagsama: "Beef Shawarma (Large)"
                'price': str(v.price),
                'is_active': v.is_active, # Ito na ang totoong status ng variant!
                'sku': v.sku if v.sku else f"PRD-{v.id}"
            })
            
        # I-attach ang list sa mismong category object
        category.serializable_items = variant_list
    
    return render(request, 'category_management.html', {
        'categories': categories
    })

@login_required
def stock_movements_list(request):
    movements = StockMovement.objects.select_related('ingredient', 'performed_by').order_by('-created_at')[:300]
    
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

    # GET Request: Kukunin lahat ng active menu variants at ingredients
    variants = MenuVariant.objects.select_related('item').filter(is_active=True).order_by('item__name', 'name')
    ingredients = Ingredient.objects.filter(is_active=True).order_by('name')

    # I-convert sa JSON format para madaling i-manipulate sa JavaScript (walang page reload)
    variants_data = []
    for v in variants:
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
        'ingredients': ingredients
    })