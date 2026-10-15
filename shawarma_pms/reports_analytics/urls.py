from django.urls import path
from . import views

app_name = "reports_analytics"

urlpatterns = [
    path("sales/", views.sales_report, name="sales_report"),
]