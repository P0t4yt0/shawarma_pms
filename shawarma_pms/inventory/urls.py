from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    path("ingredients/", views.inventory_list, name="list"),
    path("products/", views.product_management, name="products"),
    path("categories/", views.category_management, name="categories"),
    path('movements/', views.stock_movements_list, name='stock_movements'),
    path('recipes/', views.recipe_management, name='recipe_management'),
]