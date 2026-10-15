from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView  # Idagdag ito

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="security:login", permanent=False)),
    
    path("admin/", admin.site.urls),
    path("pos/", include("point_of_sale.urls")),
    path("billing/", include("billing_payment.urls")),
    path("security/", include("security.urls")),
    path('inventory/', include('inventory.urls')),
    path("promotions/", include("promotions.urls")),
    path('reports/', include('reports_analytics.urls')),
]