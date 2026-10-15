from django.urls import path
from . import views

app_name = "promotions"

urlpatterns = [
    path("list/", views.promotions_list, name="list"),
    path("history/", views.promotion_history, name="history"),
]