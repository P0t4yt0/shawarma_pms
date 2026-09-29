"""PHASE 2 - websocket URL routing (wired in shawarma_pms/asgi.py)."""
from django.urls import path
from .consumers import OrderEventsConsumer

websocket_urlpatterns = [path("ws/pos/orders/", OrderEventsConsumer.as_asgi())]
