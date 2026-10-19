from django.urls import path
from . import views

app_name = 'activity_log'

urlpatterns = [
    path('history/', views.activity_log_list, name='logs'),
]