from django.urls import path
from . import views

app_name = "reports_analytics"

urlpatterns = [
    path("sales/", views.sales_report, name="sales_report"),
    path("product-sales/", views.product_sales_report, name="product_sales_report"),
    path("inventory/", views.inventory_report, name="inventory_report"),
    path("sales-forecast/", views.sales_forecast_report, name="sales_forecast_report"),
    path("stock-spoilage/", views.stock_spoilage_report, name="stock_spoilage_report"),
]