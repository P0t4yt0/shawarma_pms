from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    path("ingredients/", views.inventory_list, name="list"),
    path("products/", views.product_management, name="products"),
]