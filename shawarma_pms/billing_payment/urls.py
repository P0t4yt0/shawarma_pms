from django.urls import path
from . import views

app_name = "billing"
urlpatterns = [
    path("api/orders/<int:order_id>/pay/", views.api_pay, name="api_pay"),
    path("orders/<int:order_id>/receipt/", views.receipt_text, name="receipt_text"),
]
