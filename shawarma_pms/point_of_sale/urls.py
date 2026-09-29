from django.urls import path
from . import views

app_name = "pos"
urlpatterns = [
    path("", views.pos_screen, name="pos_screen"),
    path("kds/", views.kds_screen, name="kds_screen"),
    path("api/kds/tickets/", views.api_kds_tickets, name="api_kds_tickets"),
    path("api/orders/ready/", views.api_ready_orders, name="api_ready_orders"),
    path("api/orders/", views.api_create_order, name="api_create_order"),
    path("api/orders/<int:pk>/status/", views.api_order_status, name="api_order_status"),
    # Phase 2: websocket routes live in point_of_sale/routing.py (not HTTP urls)
]
