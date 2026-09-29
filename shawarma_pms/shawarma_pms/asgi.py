"""ASGI entry point.

PHASE 1: plain Django ASGI app (polling needs nothing more).
PHASE 2: uncomment the ProtocolTypeRouter block below after `pip install channels channels-redis daphne`
         and adding "daphne"/"channels" to INSTALLED_APPS + CHANNEL_LAYERS to settings.
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shawarma_pms.settings")
django_asgi_app = get_asgi_application()
application = django_asgi_app

# from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.auth import AuthMiddlewareStack
# from point_of_sale.routing import websocket_urlpatterns
# application = ProtocolTypeRouter({
#     "http": django_asgi_app,
#     "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
# })
