import json
from django.shortcuts import render
from django.http import JsonResponse
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.utils.text import slugify

from .models import Ingredient, LowStockAlert
from point_of_sale.models import Category, MenuItem, MenuVariant

@login_required
def inventory_list(request):
    ingredients = Ingredient.objects.filter(is_active=True).order_by('category', 'name')
    
    active_alerts = LowStockAlert.objects.filter(status=LowStockAlert.Status.OPEN).select_related('ingredient')

    context = {
        'ingredients': ingredients,
        'alerts': active_alerts,
    }
    return render(request, 'inventory_list.html', context)

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
    categories = Category.objects.prefetch_related('items').order_by('name')
    
    return render(request, 'category_management.html', {
        'categories': categories
    })