from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pos/", include("point_of_sale.urls")),
    path("billing/", include("billing_payment.urls")),
    path("security/", include("security.urls")),
]
